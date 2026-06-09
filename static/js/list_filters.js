/**
 * List pages: export with full filter form; sort links keep current form field values.
 */
(function () {
    function filterForm() {
        return document.querySelector("form.filter-form[id], form.filter-form");
    }

    function mergeFormIntoUrl(href, form) {
        if (!form) {
            return href;
        }
        const target = new URL(href, window.location.origin);
        const params = new FormData(form);
        params.forEach(function (value, key) {
            if (key === "export") {
                return;
            }
            if (value !== "") {
                target.searchParams.set(key, value);
            }
        });
        return target.toString();
    }

    document.addEventListener("DOMContentLoaded", function () {
        const form = filterForm();

        document.querySelectorAll(".js-export-with-form[data-export-format]").forEach(function (btn) {
            btn.addEventListener("click", function (ev) {
                ev.preventDefault();
                const fmt = btn.getAttribute("data-export-format");
                const targetForm =
                    (btn.getAttribute("form") && document.getElementById(btn.getAttribute("form"))) || form;
                if (!targetForm) {
                    return;
                }
                const params = new FormData(targetForm);
                params.set("export", fmt);
                const action = targetForm.getAttribute("action") || window.location.pathname;
                const qs = params.toString();
                window.location = qs ? action + "?" + qs : action;
            });
        });

        document.querySelectorAll("a.table-sort-link").forEach(function (link) {
            link.addEventListener("click", function (ev) {
                if (!form) {
                    return;
                }
                ev.preventDefault();
                window.location = mergeFormIntoUrl(link.getAttribute("href"), form);
            });
        });

        document
            .querySelectorAll(
                "#mouse-list-filters select.filter-control:not([name='per_page']), " +
                "#cage-list-filters select.filter-control:not([name='per_page'])"
            )
            .forEach(function (select) {
                select.addEventListener("change", function () {
                    if (!select.form) {
                        return;
                    }
                    select.form.requestSubmit();
                });
            });
    });
})();
