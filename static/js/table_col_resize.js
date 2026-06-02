/**
 * Excel-style resize for the Genotype Summary column (Mice list).
 */
(() => {
    const STORAGE_PREFIX = "mousexgene:genotype-col-width:";
    const DEFAULT_GENOTYPE_WIDTH = 176;

    function tableKey(table) {
        return table.dataset.colResizeKey || table.id || "default";
    }

    function genotypeCol(table) {
        return table.querySelector("colgroup col.col-w-genotype");
    }

    function genotypeHeader(table) {
        return table.querySelector("thead th.col-genotype");
    }

    function updateGenotypeOverflow(table) {
        table.querySelectorAll(".col-genotype-cell").forEach((cell) => {
            const text = cell.querySelector(".col-genotype-text");
            if (!text) {
                cell.classList.remove("is-truncated");
                return;
            }
            const truncated = text.scrollWidth > text.clientWidth + 1;
            cell.classList.toggle("is-truncated", truncated);
        });
    }

    function readColWidth(col) {
        const inline = parseInt(String(col.style.width || ""), 10);
        if (Number.isFinite(inline) && inline > 0) {
            return inline;
        }
        const computed = parseInt(window.getComputedStyle(col).width || "", 10);
        if (Number.isFinite(computed) && computed > 0) {
            return computed;
        }
        return DEFAULT_GENOTYPE_WIDTH;
    }

    function setGenotypeWidth(table, col, widthPx) {
        const minWidth = Number.parseInt(col.dataset.minWidth || "72", 10);
        const next = Math.max(minWidth, Math.round(widthPx));
        const widthValue = `${next}px`;
        col.style.width = widthValue;
        table.style.setProperty("--genotype-col-width", widthValue);
        const header = genotypeHeader(table);
        if (header) {
            header.style.width = widthValue;
            header.style.minWidth = widthValue;
            header.style.maxWidth = widthValue;
        }
        table.querySelectorAll("td.col-genotype").forEach((td) => {
            td.style.width = widthValue;
            td.style.minWidth = widthValue;
            td.style.maxWidth = widthValue;
        });
        updateGenotypeOverflow(table);
        return next;
    }

    function loadGenotypeWidth(table, col) {
        const raw = localStorage.getItem(STORAGE_PREFIX + tableKey(table));
        if (raw) {
            const saved = parseInt(raw, 10);
            if (Number.isFinite(saved) && saved >= 72) {
                setGenotypeWidth(table, col, saved);
                return;
            }
        }
        setGenotypeWidth(table, col, DEFAULT_GENOTYPE_WIDTH);
    }

    function saveGenotypeWidth(table, col) {
        localStorage.setItem(STORAGE_PREFIX + tableKey(table), String(readColWidth(col)));
    }

    function watchGenotypeOverflow(table) {
        updateGenotypeOverflow(table);
        if (typeof ResizeObserver === "undefined") {
            window.addEventListener("resize", () => updateGenotypeOverflow(table));
            return;
        }
        const observer = new ResizeObserver(() => updateGenotypeOverflow(table));
        const header = genotypeHeader(table);
        if (header) {
            observer.observe(header);
        }
        table.querySelectorAll("td.col-genotype").forEach((node) => observer.observe(node));
    }

    function initTable(table) {
        const col = genotypeCol(table);
        const th = genotypeHeader(table);
        if (!col || !th) {
            return;
        }

        if (table.dataset.genotypeColResizeReady !== "1") {
            table.dataset.genotypeColResizeReady = "1";
            loadGenotypeWidth(table, col);
            watchGenotypeOverflow(table);

            th.classList.add("th-resizable");

            const resizer = document.createElement("span");
            resizer.className = "col-resizer col-resizer--genotype";
            resizer.setAttribute("role", "separator");
            resizer.setAttribute("aria-orientation", "vertical");
            resizer.setAttribute("aria-label", "Resize Genotype Summary column");
            resizer.title = "Drag to resize column";
            th.appendChild(resizer);

            resizer.addEventListener("mousedown", (event) => {
                event.preventDefault();
                event.stopPropagation();

                const startX = event.pageX;
                const startWidth = readColWidth(col);

                document.body.classList.add("col-resize-active");

                const onMove = (moveEvent) => {
                    const delta = moveEvent.pageX - startX;
                    setGenotypeWidth(table, col, startWidth + delta);
                };

                const onUp = () => {
                    document.body.classList.remove("col-resize-active");
                    document.removeEventListener("mousemove", onMove);
                    document.removeEventListener("mouseup", onUp);
                    saveGenotypeWidth(table, col);
                    updateGenotypeOverflow(table);
                };

                document.addEventListener("mousemove", onMove);
                document.addEventListener("mouseup", onUp);
            });

            window.addEventListener("load", () => updateGenotypeOverflow(table));
        } else {
            updateGenotypeOverflow(table);
        }
    }

    function initAll(root = document) {
        root.querySelectorAll(".data-table--col-resize").forEach(initTable);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () => initAll());
    } else {
        initAll();
    }

    window.MouseXGeneTableColResize = { initAll, updateGenotypeOverflow };
})();
