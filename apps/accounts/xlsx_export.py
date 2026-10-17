"""Dependency-free, bounded-memory XLSX writer for admin emergency exports.

This is deliberately a small OOXML exporter rather than introducing an
unrelated runtime dependency. Values are always written as inline strings,
never formulas (even when a Telegram username begins with `=`).
"""

from tempfile import SpooledTemporaryFile
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from django.http import FileResponse

HEADERS = (
    "شماره تلفن", "شناسه عددی تلگرام", "نام کامل", "نام کاربری",
    "برند", "وضعیت اشتراک", "تعداد اشتراک فعال", "سرویس‌های فعال",
    "تاریخ انقضای نزدیک‌ترین اشتراک", "تاریخ عضویت",
)


def _safe_xml(value):
    if value is None:
        value = ""
    # XML 1.0 forbids most C0 control characters; preserve text/phone zeroes.
    text = str(value)
    text = "".join(
        ch for ch in text
        if (ch in "\t\n\r" or ord(ch) >= 32)
        and not 0xD800 <= ord(ch) <= 0xDFFF
        and ord(ch) not in (0xFFFE, 0xFFFF)
    )
    return escape(text, {'"': '&quot;', "'": '&apos;'})


def _column(index):
    result = ""
    while index:
        index, digit = divmod(index - 1, 26)
        result = chr(65 + digit) + result
    return result


def users_xlsx_response(users, *, filename="emergency_users.xlsx"):
    """Create an Excel file with filters, frozen header and readable columns.

    `users` is an iterator of ready-to-write rows; no personal data is sent to
    external spreadsheet services. SpooledTemporaryFile limits memory use.
    """
    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=4) as archive:
        archive.writestr("[Content_Types].xml", '''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>''')
        archive.writestr("_rels/.rels", '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''')
        archive.writestr("xl/workbook.xml", '''<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Users" sheetId="1" r:id="rId1"/></sheets></workbook>''')
        archive.writestr("xl/_rels/workbook.xml.rels", '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>''')
        archive.writestr("xl/styles.xml", '''<?xml version="1.0" encoding="UTF-8"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Arial"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF164E63"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>''')
        with archive.open("xl/worksheets/sheet1.xml", "w") as sheet:
            def emit(value):
                sheet.write(value.encode("utf-8"))

            emit('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0" rightToLeft="1"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>')
            for idx, width in enumerate((22, 22, 26, 25, 19, 20, 22, 40, 30, 28), 1):
                emit(f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>')
            emit('</cols><sheetData>')
            row_number = 0
            for row_number, row in enumerate(_with_headers(users), 1):
                if row_number > 1048576:
                    raise ValueError("Excel sheet row limit exceeded")
                header_height = ' ht="27" customHeight="1"' if row_number == 1 else ""
                emit(f'<row r="{row_number}"{header_height}>')
                for idx, value in enumerate(row, 1):
                    col = _column(idx)
                    header_style = ' s="1"' if row_number == 1 else ""
                    emit(f'<c r="{col}{row_number}" t="inlineStr"{header_style}><is><t xml:space="preserve">{_safe_xml(value)}</t></is></c>')
                emit('</row>')
            emit('</sheetData>')
            emit(f'<autoFilter ref="A1:J{max(1, row_number)}"/>')
            emit('</worksheet>')
    output.seek(0)
    return FileResponse(
        output,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        filename=filename,
    )


def _with_headers(rows):
    yield HEADERS
    yield from rows
