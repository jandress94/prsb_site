(function () {
    const form = document.getElementById("bulk-edit-form");
    if (!form) {
        return;
    }

    const members = JSON.parse(document.getElementById("bulk-members").textContent);
    const instruments = JSON.parse(document.getElementById("bulk-instruments").textContent);

    function catalogName(id) {
        const member = members.find(function (item) { return String(item.id) === String(id); });
        return member ? member.name : String(id);
    }

    function initPicker(picker) {
        const selected = (picker.dataset.selected || "").split(",").filter(Boolean);
        picker.textContent = "";
        const chips = document.createElement("div");
        chips.className = "member-chips";
        const input = document.createElement("input");
        input.type = "text";
        input.autocomplete = "off";
        input.setAttribute("aria-label", "Add member");
        const menu = document.createElement("ul");
        menu.className = "member-picker-menu";
        menu.hidden = true;
        picker.append(chips, input, menu);

        function renderChips() {
            chips.textContent = "";
            selected.forEach(function (id) {
                const chip = document.createElement("span");
                chip.className = "member-chip";
                chip.dataset.memberId = String(id);
                chip.append(document.createTextNode(catalogName(id) + " "));
                const remove = document.createElement("button");
                remove.type = "button";
                remove.textContent = "×";
                remove.setAttribute("aria-label", "Remove " + catalogName(id));
                remove.addEventListener("click", function () {
                    const index = selected.indexOf(String(id));
                    if (index !== -1) {
                        selected.splice(index, 1);
                    }
                    renderChips();
                    renderMenu();
                });
                chip.append(remove);
                chips.append(chip);
            });
        }

        function renderMenu() {
            menu.textContent = "";
            const query = input.value.trim().toLowerCase();
            members.forEach(function (member) {
                const id = String(member.id);
                if (selected.indexOf(id) !== -1) {
                    return;
                }
                if (query && member.name.toLowerCase().indexOf(query) === -1) {
                    return;
                }
                const item = document.createElement("li");
                const button = document.createElement("button");
                button.type = "button";
                button.textContent = member.name;
                button.addEventListener("mousedown", function (event) {
                    event.preventDefault();
                });
                button.addEventListener("click", function () {
                    selected.push(id);
                    input.value = "";
                    renderChips();
                    renderMenu();
                    input.focus();
                });
                item.append(button);
                menu.append(item);
            });
        }

        input.addEventListener("focus", function () {
            renderMenu();
            menu.hidden = false;
        });
        input.addEventListener("input", function () {
            renderMenu();
            menu.hidden = false;
        });
        input.addEventListener("keydown", function (event) {
            if (event.key === "Backspace" && input.value === "" && selected.length) {
                event.preventDefault();
                selected.pop();
                renderChips();
                renderMenu();
            }
        });
        document.addEventListener("click", function (event) {
            if (!picker.contains(event.target)) {
                menu.hidden = true;
            }
        });
        renderChips();
    }

    function usedInstrumentIds(part, exceptSelect) {
        const used = new Set();
        part.querySelectorAll(".bulk-row[data-kind='existing']").forEach(function (row) {
            if (row.dataset.instrumentId) {
                used.add(String(row.dataset.instrumentId));
            }
        });
        part.querySelectorAll(".bulk-row[data-kind='new'] select").forEach(function (select) {
            if (select !== exceptSelect && select.value) {
                used.add(select.value);
            }
        });
        return used;
    }

    function fillInstrumentSelect(select, part) {
        const current = select.value;
        const used = usedInstrumentIds(part, select);
        select.textContent = "";
        const blank = document.createElement("option");
        blank.value = "";
        blank.textContent = "---------";
        select.append(blank);
        instruments.forEach(function (instrument) {
            const id = String(instrument.id);
            if (used.has(id) && id !== current) {
                return;
            }
            const option = document.createElement("option");
            option.value = id;
            option.textContent = instrument.name;
            if (id === current) {
                option.selected = true;
            }
            select.append(option);
        });
    }

    function refreshInstrumentSelects(part) {
        part.querySelectorAll(".bulk-row[data-kind='new'] select").forEach(function (select) {
            fillInstrumentSelect(select, part);
        });
    }

    function bindNewRow(row, part) {
        const select = row.querySelector("select");
        select.addEventListener("change", function () {
            refreshInstrumentSelects(part);
        });
        row.querySelector(".bulk-remove-row").addEventListener("click", function () {
            row.remove();
            refreshInstrumentSelects(part);
        });
        fillInstrumentSelect(select, part);
        initPicker(row.querySelector(".member-picker"));
    }

    function addRow(part) {
        const row = document.createElement("div");
        row.className = "bulk-row";
        row.dataset.kind = "new";
        const select = document.createElement("select");
        select.className = "bulk-instrument-select";
        const picker = document.createElement("div");
        picker.className = "member-picker";
        picker.dataset.selected = "";
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "bulk-remove-row";
        remove.textContent = "Remove row";
        row.append(select, picker, remove);
        part.querySelector(".bulk-new-rows").append(row);
        bindNewRow(row, part);
    }

    function appendField(name, value) {
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = name;
        input.value = value;
        input.dataset.generatedField = "1";
        form.append(input);
    }

    form.addEventListener("submit", function () {
        form.querySelectorAll("[data-server-fields]").forEach(function (field) {
            field.remove();
        });
        form.querySelectorAll("[data-generated-field]").forEach(function (field) {
            field.remove();
        });
        let existingIndex = 0;
        let newIndex = 0;
        form.querySelectorAll(".bulk-part").forEach(function (part) {
            const partId = part.dataset.partId;
            part.querySelectorAll(".bulk-row[data-kind='existing']").forEach(function (row) {
                appendField("existing-" + existingIndex + "-part", partId);
                appendField("existing-" + existingIndex + "-instrument", row.dataset.instrumentId);
                row.querySelectorAll(".member-chip").forEach(function (chip) {
                    appendField("existing-" + existingIndex + "-members", chip.dataset.memberId);
                });
                existingIndex += 1;
            });
            part.querySelectorAll(".bulk-row[data-kind='new']").forEach(function (row) {
                appendField("new-" + newIndex + "-part", partId);
                appendField("new-" + newIndex + "-instrument", row.querySelector("select").value);
                row.querySelectorAll(".member-chip").forEach(function (chip) {
                    appendField("new-" + newIndex + "-members", chip.dataset.memberId);
                });
                newIndex += 1;
            });
        });
        appendField("existing-TOTAL", String(existingIndex));
        appendField("new-TOTAL", String(newIndex));
    });

    form.querySelectorAll(".bulk-part").forEach(function (part) {
        part.querySelectorAll(".bulk-row[data-kind='existing'] .member-picker").forEach(initPicker);
        part.querySelectorAll(".bulk-row[data-kind='new']").forEach(function (row) {
            bindNewRow(row, part);
        });
        part.querySelector(".bulk-add-row").addEventListener("click", function () {
            addRow(part);
        });
    });
})();
