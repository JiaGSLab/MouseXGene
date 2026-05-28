/**
 * Build export URLs from all fields in a filter form (including rows below the export button).
 */
(function () {
    function exportFromForm(btn) {
        const formId = btn.getAttribute("form");
        const form = formId ? document.getElementById(formId) : btn.closest("form");
        if (!form) {
            return;
        }
        const fmt = btn.getAttribute("data-export-format");
        if (!fmt) {
            return;
        }
        const params = new URLSearchParams(new FormData(form));
        params.set("export", fmt);
        const action = form.getAttribute("action") || window.location.pathname;
        const qs = params.toString();
        window.location = qs ? `${action}?${qs}` : action;
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("[data-export-format]").forEach(function (btn) {
            btn.addEventListener("click", function (ev) {
                ev.preventDefault();
                exportFromForm(btn);
            });
        });
    });
})();
