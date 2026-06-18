from __future__ import annotations

import csv
from io import BytesIO
from typing import Iterable

from django.http import HttpResponse
from django.utils.http import content_disposition_header
from openpyxl import Workbook


FORMULA_PREFIXES = ("=", "+", "-", "@")


def escape_spreadsheet_cell(value):
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return f"'{value}"
    return value


def safe_spreadsheet_row(row: Iterable) -> list:
    return [escape_spreadsheet_cell(value) for value in row]


def safe_spreadsheet_rows(rows: Iterable[Iterable]) -> list[list]:
    return [safe_spreadsheet_row(row) for row in rows]


def set_content_disposition(response: HttpResponse, filename: str, *, as_attachment: bool = True) -> HttpResponse:
    response["Content-Disposition"] = content_disposition_header(as_attachment, filename)
    response["X-Content-Type-Options"] = "nosniff"
    return response


def csv_response(filename: str, headers: list[str], rows: Iterable[Iterable]) -> HttpResponse:
    response = HttpResponse(content_type="text/csv")
    set_content_disposition(response, filename)
    writer = csv.writer(response)
    writer.writerow(safe_spreadsheet_row(headers))
    writer.writerows(safe_spreadsheet_rows(rows))
    return response


def xlsx_response(filename: str, sheet_name: str, headers: list[str], rows: Iterable[Iterable]) -> HttpResponse:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet_name
    worksheet.append(safe_spreadsheet_row(headers))
    for row in rows:
        worksheet.append(safe_spreadsheet_row(row))
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    response = HttpResponse(
        stream.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    return set_content_disposition(response, filename)
