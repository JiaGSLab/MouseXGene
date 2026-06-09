(() => {
    function buildQuery(params) {
        const q = new URLSearchParams();
        Object.entries(params).forEach(([key, value]) => {
            if (value !== undefined && value !== null && String(value).trim() !== "") {
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
        async loadMouseStrainMap(apiUrl) {
            return fetchJson(apiUrl);
        },
        renderCageSelect(cageSelect, cages, selectedId) {
            const current = cageSelect.value || selectedId || "";
            cageSelect.innerHTML = '<option value="">---------</option>';
            for (const cage of cages) {
                const opt = document.createElement("option");
                opt.value = String(cage.id);
                opt.textContent = cage.is_empty ? `${cage.cage_id} (empty)` : cage.cage_id;
                if (String(cage.id) === String(current)) opt.selected = true;
                cageSelect.appendChild(opt);
            }
        },
        lookupCages(cages, query) {
            const q = String(query || "").trim().toLowerCase();
            if (!q) return [];
            return cages.filter((c) => String(c.cage_id || "").toLowerCase().includes(q));
        },
    };
})();
