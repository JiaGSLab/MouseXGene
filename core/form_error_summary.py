def form_error_summary(form, *, prefix: str = "") -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if form is None or not getattr(form, "is_bound", False):
        return rows

    for error in form.non_field_errors():
        rows.append({"label": prefix or "Form", "message": str(error), "target": ""})

    for field_name, errors in form.errors.items():
        if field_name == "__all__":
            continue
        field = form.fields.get(field_name)
        if field is None:
            label = field_name.replace("_", " ").title()
            target = ""
        else:
            label = field.label or field_name.replace("_", " ").title()
            target = form[field_name].id_for_label or ""
        if prefix:
            label = f"{prefix} - {label}"
        for error in errors:
            rows.append({"label": label, "message": str(error), "target": target})
    return rows


def forms_error_summary(forms, *, prefix: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, form in enumerate(forms, start=1):
        rows.extend(form_error_summary(form, prefix=f"{prefix} {index}"))
    return rows


def formset_error_summary(formset, *, prefix: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if formset is None:
        return rows
    if getattr(formset, "is_bound", False):
        for error in formset.non_form_errors():
            rows.append({"label": prefix, "message": str(error), "target": ""})
    rows.extend(forms_error_summary(formset.forms, prefix=prefix))
    return rows
