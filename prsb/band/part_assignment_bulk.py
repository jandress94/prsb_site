from dataclasses import dataclass

from django.db import transaction

from .models import BandMember, Instrument, PartAssignment, PerformanceReadiness, SongPart

ERR_INSTRUMENT_REQUIRED = "Choose an instrument for each new row that has members."
ERR_DUPLICATE_INSTRUMENT = "Each instrument can only appear once on a part."
ERR_MEMBER = "A selected member is not in this band."
ERR_PART = "A part on this form is not on this song."
ERR_INSTRUMENT = "Choose a real instrument."
ERR_EXISTING = "That part and instrument is not an existing row for this band."


@dataclass(frozen=True)
class PostedRow:
    kind: str
    part_id: int
    instrument_id: int | None
    member_ids: tuple[int, ...]


def active_members(band):
    return BandMember.objects.filter(
        bandmembership__band=band,
        bandmembership__is_active=True,
        user__is_active=True,
    ).select_related("user").order_by("user__first_name", "user__last_name")


def _total(post, key):
    raw = post.get(key, "0")
    if raw is None or not str(raw).isdigit():
        return 0
    return int(raw)


def _int_or_missing(raw):
    if raw is None or raw == "":
        return None
    if str(raw).isdigit():
        return int(raw)
    return -1


def parse_posted_rows(post):
    rows = []
    for kind in ("existing", "new"):
        for index in range(_total(post, f"{kind}-TOTAL")):
            prefix = f"{kind}-{index}-"
            member_ids = []
            for raw in post.getlist(prefix + "members"):
                parsed = _int_or_missing(raw)
                member_ids.append(-1 if parsed is None else parsed)
            part_id = _int_or_missing(post.get(prefix + "part", ""))
            rows.append(PostedRow(
                kind=kind,
                part_id=-1 if part_id is None else part_id,
                instrument_id=_int_or_missing(post.get(prefix + "instrument", "")),
                member_ids=tuple(member_ids),
            ))
    return rows


def _dedupe(row):
    seen = set()
    member_ids = []
    for member_id in row.member_ids:
        if member_id in seen:
            continue
        seen.add(member_id)
        member_ids.append(member_id)
    return PostedRow(row.kind, row.part_id, row.instrument_id, tuple(member_ids))


def _add_error(errors, message):
    if message not in errors:
        errors.append(message)


def apply_bulk_part_assignments(song, band, rows):
    prepared = []
    for row in rows:
        row = _dedupe(row)
        if row.kind == "new" and not row.member_ids:
            continue
        prepared.append(row)

    members = list(active_members(band))
    active_ids = {member.pk for member in members}
    part_ids = set(SongPart.objects.filter(song=song).values_list("pk", flat=True))
    instrument_ids = set(Instrument.objects.values_list("pk", flat=True))
    current_by_key = {}
    for assignment in PartAssignment.objects.filter(song_part__song=song, member_id__in=active_ids):
        current_by_key.setdefault((assignment.song_part_id, assignment.instrument_id), {})[assignment.member_id] = assignment

    errors = []
    seen_keys = set()
    for row in prepared:
        if row.part_id not in part_ids:
            _add_error(errors, ERR_PART)
        if row.instrument_id is None:
            if row.kind == "new" and row.member_ids:
                _add_error(errors, ERR_INSTRUMENT_REQUIRED)
        elif row.instrument_id not in instrument_ids:
            _add_error(errors, ERR_INSTRUMENT)
        for member_id in row.member_ids:
            if member_id not in active_ids:
                _add_error(errors, ERR_MEMBER)
        if row.instrument_id is not None and row.instrument_id in instrument_ids and row.part_id in part_ids:
            key = (row.part_id, row.instrument_id)
            if key in seen_keys:
                _add_error(errors, ERR_DUPLICATE_INSTRUMENT)
            seen_keys.add(key)
        if row.kind == "existing" and (row.part_id, row.instrument_id) not in current_by_key:
            _add_error(errors, ERR_EXISTING)

    if errors:
        return errors

    with transaction.atomic():
        for row in prepared:
            if row.kind == "existing":
                current = current_by_key[(row.part_id, row.instrument_id)]
                posted = set(row.member_ids)
                for member_id, assignment in current.items():
                    if member_id not in posted:
                        assignment.delete()
                for member_id in row.member_ids:
                    if member_id in current:
                        continue
                    PartAssignment.objects.create(
                        member_id=member_id,
                        song_part_id=row.part_id,
                        instrument_id=row.instrument_id,
                        performance_readiness=PerformanceReadiness.READY,
                        can_solo=False,
                    )
            else:
                for member_id in row.member_ids:
                    if PartAssignment.objects.filter(
                        member_id=member_id,
                        song_part_id=row.part_id,
                        instrument_id=row.instrument_id,
                    ).exists():
                        continue
                    PartAssignment.objects.create(
                        member_id=member_id,
                        song_part_id=row.part_id,
                        instrument_id=row.instrument_id,
                        performance_readiness=PerformanceReadiness.READY,
                        can_solo=False,
                    )
    return []


def _chips(member_ids, names):
    return [{"id": member_id, "name": names.get(member_id, str(member_id))} for member_id in member_ids]


def _row_payload(index, instrument_id, instrument_name, member_ids, names):
    chips = _chips(member_ids, names)
    return {
        "index": index,
        "instrument_id": instrument_id,
        "instrument_name": instrument_name,
        "chips": chips,
        "selected_ids": ",".join(str(chip["id"]) for chip in chips),
    }


def bulk_edit_page(song, band, rows=None):
    members = list(active_members(band))
    names = {member.pk: member.user.get_full_name() for member in members}
    instruments = list(Instrument.objects.all())
    instrument_by_id = {instrument.pk: instrument for instrument in instruments}
    parts = list(SongPart.objects.filter(song=song).order_by("_order"))

    db_members = {}
    for assignment in PartAssignment.objects.filter(song_part__song=song, member_id__in=names.keys()):
        db_members.setdefault((assignment.song_part_id, assignment.instrument_id), set()).add(assignment.member_id)

    posted_existing = {}
    posted_new = {}
    if rows is not None:
        for row in rows:
            if row.kind == "existing":
                posted_existing[(row.part_id, row.instrument_id)] = row.member_ids
            else:
                posted_new.setdefault(row.part_id, []).append(row)

    def member_ids_for(key):
        if rows is None or key not in posted_existing:
            return [member.pk for member in members if member.pk in db_members.get(key, ())]
        return list(posted_existing[key])

    sections = []
    existing_index = 0
    new_index = 0
    for part in parts:
        keys = [key for key in db_members if key[0] == part.pk]
        keys.sort(key=lambda key: instrument_by_id[key[1]].order if key[1] in instrument_by_id else key[1])
        seen = set(keys)
        for key in posted_existing:
            if key[0] == part.pk and key not in seen:
                keys.append(key)
                seen.add(key)
        existing_rows = []
        for key in keys:
            instrument = instrument_by_id.get(key[1])
            existing_rows.append(_row_payload(
                existing_index,
                key[1],
                instrument.name if instrument is not None else str(key[1]),
                member_ids_for(key),
                names,
            ))
            existing_index += 1
        new_rows = []
        for row in posted_new.get(part.pk, []):
            instrument = instrument_by_id.get(row.instrument_id)
            new_rows.append(_row_payload(
                new_index,
                row.instrument_id,
                instrument.name if instrument is not None else "",
                row.member_ids,
                names,
            ))
            new_index += 1
        sections.append({"part": part, "existing_rows": existing_rows, "new_rows": new_rows})

    return {
        "picker_members": [{"id": member.pk, "name": names[member.pk]} for member in members],
        "picker_instruments": [{"id": instrument.pk, "name": instrument.name} for instrument in instruments],
        "sections": sections,
        "existing_total": existing_index,
        "new_total": new_index,
    }
