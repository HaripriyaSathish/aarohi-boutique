"""Idempotent paid-order invoice snapshots; PDF is rendered without public media."""
import os
from io import BytesIO
from xml.sax.saxutils import escape
from .services import ShopError


def issue_invoice(order):
    from .models import InvoiceDocument, StoreSettings
    if order.payment_status not in ('paid', 'partial_refund', 'refunded'):
        raise ShopError('Invoice available after verified payment.')
    store = StoreSettings.objects.first()
    document, _ = InvoiceDocument.objects.get_or_create(order=order, defaults={'snapshot': {
        'number': order.number, 'date': order.created.isoformat(),
        'store': store.name if store else 'Aarohi Boutique',
        'store_address': store.address if store else '',
        'delivery': order.shipping_address,
        'items': [{'name': i.name, 'size': i.size, 'colour': i.colour,
                   'quantity': i.quantity, 'price': str(i.price),
                   'total': str(i.price * i.quantity)} for i in order.items.all()],
        'subtotal': str(order.subtotal), 'discount': str(order.discount),
        'shipping': str(order.shipping), 'total': str(order.total),
        'payment_reference': order.provider_payment,
    }})
    return document


# (regular, bold) fonts that contain the rupee glyph; first one found is used.
FONT_CANDIDATES = [
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]
SERIF_CANDIDATES = ["C:/Windows/Fonts/georgia.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"]


def _fonts():
    """Register a Unicode font once; fall back to Helvetica and 'Rs.'."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if "AB-Sans" in pdfmetrics.getRegisteredFontNames():
        return "AB-Sans", "AB-Sans-Bold", "AB-Serif", "\u20b9"
    for regular, bold in FONT_CANDIDATES:
        if os.path.exists(regular) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont("AB-Sans", regular))
            pdfmetrics.registerFont(TTFont("AB-Sans-Bold", bold))
            serif = next((f for f in SERIF_CANDIDATES if os.path.exists(f)), bold)
            pdfmetrics.registerFont(TTFont("AB-Serif", serif))
            return "AB-Sans", "AB-Sans-Bold", "AB-Serif", "\u20b9"
    return "Helvetica", "Helvetica-Bold", "Times-Bold", "Rs. "


def invoice_pdf(document):
    """Full-colour branded invoice (plum / lavender / gold) as PDF bytes."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    sans, bold, serif, rs = _fonts()
    data, output = document.snapshot, BytesIO()
    hexc = colors.HexColor
    PLUM, ROYAL, VIOLET, LAV, LAV2, GOLD, INK, MUTED, GREEN, LINE = (
        hexc("#2b1638"), hexc("#653a88"), hexc("#8657b0"), hexc("#f1e9f9"), hexc("#faf6fd"),
        hexc("#c8a165"), hexc("#2b2233"), hexc("#7a7084"), hexc("#2f8a57"), hexc("#e2d4f1"),
    )
    W, H = A4

    def st(size=10, font=sans, color=INK, align=0):
        return ParagraphStyle("s", fontName=font, fontSize=size, leading=size * 1.45, textColor=color, alignment=align)

    def P(text, style):
        return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)

    def money(v):
        return f"{rs}{v}"

    def page(canvas, doc):
        canvas.saveState()
        # Header: layered plum -> royal -> violet bands with a gold rule
        canvas.setFillColor(PLUM); canvas.rect(0, H - 128, W, 128, stroke=0, fill=1)
        canvas.setFillColor(ROYAL); canvas.rect(W * 0.55, H - 128, W * 0.45, 128, stroke=0, fill=1)
        canvas.setFillColor(VIOLET); canvas.rect(W * 0.82, H - 128, W * 0.18, 128, stroke=0, fill=1)
        canvas.setFillColor(GOLD); canvas.rect(0, H - 132, W, 4, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont(serif, 26); canvas.drawString(40, H - 62, data["store"])
        canvas.setFont(sans, 8.5); canvas.drawString(42, H - 80, "WOMEN'S WEAR, BEAUTIFULLY CURATED")
        canvas.setFont(bold, 24); canvas.drawRightString(W - 40, H - 60, "INVOICE")
        canvas.setFont(sans, 10)
        canvas.drawRightString(W - 40, H - 80, f"No. {data['number']}")
        canvas.drawRightString(W - 40, H - 95, f"Date {data['date'][:10]}")
        canvas.restoreState()
        # Footer
        canvas.saveState()
        canvas.setFillColor(LAV); canvas.rect(0, 0, W, 54, stroke=0, fill=1)
        canvas.setFillColor(GOLD); canvas.rect(0, 54, W, 2, stroke=0, fill=1)
        canvas.setFillColor(ROYAL); canvas.setFont(bold, 10.5)
        canvas.drawCentredString(W / 2, 32, "Thank you for shopping with " + data["store"] + "!")
        canvas.setFillColor(MUTED); canvas.setFont(sans, 8)
        canvas.drawCentredString(W / 2, 17, "Paid-order receipt. GST is not calculated; configure tax fields before statutory use.")
        canvas.restoreState()

    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=160,
                            bottomMargin=80, title=f"Invoice {data['number']}", author=data["store"])
    story = []
    label = st(8.5, bold, ROYAL)

    seller = data["store"] + ("\n" + data["store_address"] if data.get("store_address") else "")
    addr = Table([[P("SOLD BY", label), P("BILL / SHIP TO", label)],
                  [P(seller, st(10)), P(data["delivery"], st(10))]],
                 colWidths=[(W - 80) / 2 - 8] * 2, hAlign="LEFT", spaceBefore=0)
    addr.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LAV2),
        ("BOX", (0, 0), (0, -1), .6, LINE), ("BOX", (1, 0), (1, -1), .6, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story += [addr, Spacer(1, 22)]

    head = st(9.5, bold, colors.white)
    head_r = st(9.5, bold, colors.white, TA_RIGHT)
    rows = [[P("#", head), P("Item", head), P("Size / Colour", head), P("Qty", head),
             P("Unit price", head_r), P("Amount", head_r)]]
    for n, item in enumerate(data["items"], 1):
        rows.append([
            P(n, st(10, color=MUTED)), P(item["name"], st(10.5, bold)),
            P(f"{item['size']} · {item['colour']}", st(10, color=MUTED)), P(item["quantity"], st(10)),
            P(money(item["price"]), st(10, align=TA_RIGHT)), P(money(item["total"]), st(10.5, bold, align=TA_RIGHT)),
        ])
    items = Table(rows, colWidths=[24, 170, 110, 36, 85, 90], repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), ROYAL), ("LINEBELOW", (0, 0), (-1, 0), 2, GOLD),
        ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 1), (-1, -1), .4, LINE),
    ]
    style += [("BACKGROUND", (0, i), (-1, i), LAV2) for i in range(2, len(rows), 2)]
    items.setStyle(TableStyle(style))
    story += [items, Spacer(1, 16)]

    def right(v, color=INK, size=10.5, font=sans):
        return P(v, st(size, font, color, TA_RIGHT))

    delivery = "Free" if data["shipping"] in ("0", "0.00") else money(data["shipping"])
    totals = Table([
        [right("Subtotal", MUTED), right(money(data["subtotal"]))],
        [right("Discount", MUTED), right("- " + money(data["discount"]), GREEN)],
        [right("Delivery", MUTED), right(delivery)],
        [right("TOTAL PAID", colors.white, 12, bold), right(money(data["total"]), colors.white, 15, bold)],
    ], colWidths=[120, 120], hAlign="RIGHT")
    totals.setStyle(TableStyle([
        ("BACKGROUND", (0, 3), (-1, 3), PLUM), ("LINEABOVE", (0, 3), (-1, 3), 2, GOLD),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 3), (-1, 3), 11), ("BOTTOMPADDING", (0, 3), (-1, 3), 11),
    ]))
    stamp = Table([[P("PAID", st(22, bold, GREEN, 1))], [P("Payment verified", st(8.5, sans, GREEN, 1))]],
                  colWidths=[120])
    stamp.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 2.2, GREEN), ("ROUNDEDCORNERS", [8, 8, 8, 8]),
        ("BACKGROUND", (0, 0), (-1, -1), hexc("#f2fbf5")),
        ("TOPPADDING", (0, 0), (-1, 0), 8), ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
    ]))
    wrap = Table([[stamp, totals]], colWidths=[W - 80 - 250, 250])
    wrap.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (0, 0), 30)]))
    story += [wrap, Spacer(1, 22)]

    ref = data.get("payment_reference") or ""
    pay = Table([[P("PAYMENT", label), P("Verified online payment" + (f"  ·  Ref {ref}" if ref else ""), st(10))]],
                colWidths=[80, W - 160], hAlign="LEFT")
    pay.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LAV), ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(pay)
    doc.build(story, onFirstPage=page, onLaterPages=page)
    return output.getvalue()
