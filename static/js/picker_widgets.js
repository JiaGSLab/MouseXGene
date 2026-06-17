(() => {
    function buildQuery(params) {
        const q = new URLSearchParams();
        Object.entries(params).forEach(([key, value]) => {
            if (Array.isArray(value)) {
                value.forEach((item) => {
                    if (item !== undefined && item !== null && String(item).trim() !== "") {
                        q.append(key, String(item));
                    }
                });
            } else if (value !== undefined && value !== null && String(value).trim() !== "") {
                q.set(key, String(value));
            }
        });
        const text = q.toString();
        return text ? `?${text}` : "";
    }

    async function fetchJson(url) {
        const response = await fetch(url, { headers: { Accept: "application/json" } });
        if (!response.ok) {
            throw new Error(`Request failed (${response.status})`);
        }
        return response.json();
    }

    const SEX_LABELS = { M: "male", F: "female", U: "unknown" };

    function compactList(values, fallback = "") {
        const cleaned = [...new Set((values || []).map((v) => String(v || "").trim()).filter(Boolean))];
        if (!cleaned.length) return fallback;
        if (cleaned.length <= 2) return cleaned.join(", ");
        return `${cleaned.slice(0, 2).join(", ")} +${cleaned.length - 2}`;
    }

    function cageSexSummary(cage) {
        const counts = cage?.sex_counts || {};
        const parts = Object.entries(SEX_LABELS)
            .map(([value, label]) => {
                const count = Number(counts[value] || 0);
                return count ? `${count} ${label}` : "";
            })
            .filter(Boolean);
        if (parts.length) return parts.join(", ");
        const sexes = (cage?.sexes || []).map((sex) => SEX_LABELS[sex] || sex);
        return compactList(sexes, "");
    }

    function cageMouseCount(cage) {
        if (!cage) return 0;
        if (Number.isFinite(Number(cage.mouse_count))) return Number(cage.mouse_count);
        return cage.is_empty ? 0 : null;
    }

    function cageOptionLabel(cage) {
        const count = cageMouseCount(cage);
        if (!count) return `${cage.cage_id} (empty)`;
        const parts = [];
        parts.push(count === 1 ? "1 mouse" : `${count} mice`);
        const sexes = cageSexSummary(cage);
        if (sexes) parts.push(sexes);
        const strains = compactList(cage.strain_line_names || []);
        if (strains) parts.push(strains);
        return `${cage.cage_id} (${parts.join("; ")})`;
    }

    function mouseOptionLabel(mouse) {
        if (!mouse) return "";
        const uid = mouse.uid || mouse.mouse_uid || mouse.id || "";
        const parts = [];
        if (mouse.sex) parts.push(SEX_LABELS[mouse.sex] || mouse.sex);
        if (mouse.project_name) parts.push(mouse.project_name);
        if (mouse.strain_line_name) parts.push(mouse.strain_line_name);
        if (mouse.status && mouse.status !== "active") parts.push(mouse.status_label || mouse.status);
        return parts.length ? `${uid} (${parts.join("; ")})` : String(uid);
    }

    function idSet(values) {
        return new Set((values || []).map((value) => String(value || "")).filter(Boolean));
    }

    window.MXGPicker = {
        debounce(fn, delay = 300) {
            let timer = null;
            return (...args) => {
                clearTimeout(timer);
                timer = setTimeout(() => fn(...args), delay);
            };
        },
        async loadCages(apiUrl, filters = {}) {
            const data = await fetchJson(`${apiUrl}${buildQuery(filters)}`);
            return data.cages || [];
        },
        async loadMice(apiUrl, filters = {}) {
            const data = await fetchJson(`${apiUrl}${buildQuery(filters)}`);
            return data.mice || [];
        },
        async loadMouseStrainMap(apiUrl, filters = {}) {
            return fetchJson(`${apiUrl}${buildQuery(filters)}`);
        },
        renderCageSelect(cageSelect, cages, selectedId) {
            const current = cageSelect.value || selectedId || "";
            const previousLabels = {};
            Array.from(cageSelect.options || []).forEach((opt) => {
                if (opt.value) previousLabels[opt.value] = opt.textContent || opt.value;
            });
            cageSelect.innerHTML = '<option value="">---------</option>';
            const rendered = new Set();
            for (const cage of cages) {
                const opt = document.createElement("option");
                opt.value = String(cage.id);
                opt.textContent = cageOptionLabel(cage);
                if (String(cage.id) === String(current)) opt.selected = true;
                cageSelect.appendChild(opt);
                rendered.add(String(cage.id));
            }
            if (current && !rendered.has(String(current))) {
                const opt = document.createElement("option");
                opt.value = String(current);
                opt.textContent = previousLabels[String(current)] || `Selected cage #${current}`;
                opt.selected = true;
                cageSelect.appendChild(opt);
            }
        },
        renderMouseSelect(mouseSelect, mice, selectedId) {
            const current = mouseSelect.value || selectedId || "";
            const previousLabels = {};
            Array.from(mouseSelect.options || []).forEach((opt) => {
                if (opt.value) previousLabels[opt.value] = opt.textContent || opt.value;
            });
            mouseSelect.innerHTML = '<option value="">---------</option>';
            const rendered = new Set();
            for (const mouse of mice) {
                const opt = document.createElement("option");
                opt.value = String(mouse.id);
                opt.textContent = mouseOptionLabel(mouse);
                if (String(mouse.id) === String(current)) opt.selected = true;
                mouseSelect.appendChild(opt);
                rendered.add(String(mouse.id));
            }
            if (current && !rendered.has(String(current))) {
                const opt = document.createElement("option");
                opt.value = String(current);
                opt.textContent = previousLabels[String(current)] || `Selected mouse #${current}`;
                opt.selected = true;
                mouseSelect.appendChild(opt);
            }
        },
        formatMouseOption(mouse) {
            return mouseOptionLabel(mouse);
        },
        formatCageOption(cage) {
            return cageOptionLabel(cage);
        },
        lookupCages(cages, query) {
            const q = String(query || "").trim().toLowerCase();
            if (!q) return [];
            return cages.filter((c) => String(c.cage_id || "").toLowerCase().includes(q));
        },
        findCage(cages, cageId) {
            if (!cageId) return null;
            return (cages || []).find((cage) => String(cage.id) === String(cageId)) || null;
        },
        describeCage(cage) {
            if (!cage) return "";
            const useLabel = cage.cage_use_label || cage.purpose_label;
            const count = cageMouseCount(cage);
            if (!count) {
                const emptyPieces = [`${cage.cage_id} is empty`];
                if (cage.home_project_name) emptyPieces.push(`Home project: ${cage.home_project_name}`);
                if (cage.colony_name) emptyPieces.push(`Colony: ${cage.colony_name}`);
                if (useLabel) emptyPieces.push(`Use: ${useLabel}`);
                return `${emptyPieces.join(". ")}.`;
            }
            const pieces = [cageOptionLabel(cage)];
            if (cage.home_project_name) pieces.push(`Home project: ${cage.home_project_name}`);
            if (cage.colony_name) pieces.push(`Colony: ${cage.colony_name}`);
            if (useLabel) pieces.push(`Use: ${useLabel}`);
            const projects = compactList(cage.project_names || []);
            const mice = compactList(cage.mouse_uids || []);
            if (projects) pieces.push(`Projects: ${projects}`);
            if (mice) pieces.push(`Current mice: ${mice}`);
            return `${pieces.join(". ")}.`;
        },
        cageWarnings(cage, context = {}) {
            if (!cage) return [];
            const warnings = [];
            const projectIds = idSet(cage.project_ids);
            const strainIds = idSet(cage.strain_line_ids);
            if (cage.home_project_id) projectIds.add(String(cage.home_project_id));
            const cageSexes = idSet(cage.sexes);
            const projectId = String(context.projectId || "");
            const strainLineId = String(context.strainLineId || "");
            const targetSexes = idSet(context.sexes || (context.sex ? [context.sex] : []));

            if (projectId && projectIds.size && !projectIds.has(projectId)) {
                warnings.push(`selected cage already has project(s): ${compactList(cage.project_names || [])}`);
            }
            if (strainLineId && strainIds.size && !strainIds.has(strainLineId)) {
                warnings.push(`selected cage already has strain line(s): ${compactList(cage.strain_line_names || [])}`);
            }
            if (targetSexes.size && cageSexes.size) {
                const knownTargetSexes = [...targetSexes].filter((sex) => sex === "M" || sex === "F");
                const knownCageSexes = [...cageSexes].filter((sex) => sex === "M" || sex === "F");
                const targetKnown = new Set(knownTargetSexes);
                const cageKnown = new Set(knownCageSexes);
                if (
                    knownTargetSexes.length
                    && knownCageSexes.length
                    && (
                        knownTargetSexes.some((sex) => !cageKnown.has(sex))
                        || knownCageSexes.some((sex) => !targetKnown.has(sex))
                    )
                ) {
                    warnings.push(`selected cage already has ${cageSexSummary(cage)}`);
                }
            }
            return warnings;
        },
        cagePurposeWarning(cage, expectedPurpose, expectedLabel) {
            if (!cage || !expectedPurpose || !cage.purpose || cage.purpose === expectedPurpose) return "";
            const current = cage.cage_use_label || cage.purpose_label || cage.purpose;
            const target = expectedLabel || expectedPurpose;
            return `selected cage use is ${current}; saving will mark it as ${target}`;
        },
    };
})();
