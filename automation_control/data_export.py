"""CSV export -- Module 20 (the other half of it; import is a set of CLI
scripts under scripts/, matching the existing import_tcg_catalog_csv.py
convention rather than a web upload form, since bulk data entry from a
spreadsheet is an occasional back-office task, not something done from a
phone). Every export here is a plain read-only query -- nothing here writes
to the database.
"""

import csv
import io

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .database import get_session
from .models import CatalogItem, Customer, InventoryItem, Sale, SaleItem, Supplier
from .ui import brand_header, page

router = APIRouter(tags=["data-export"])


def _csv_response(filename: str, header: list[str], rows: list[list]) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return Response(
        content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/export", response_class=HTMLResponse)
def export_page(user: str = Depends(require_dashboard_user)) -> HTMLResponse:
    links = "".join(
        f"<li><a href='/export/{name}.csv'>{label}</a></li>"
        for name, label in [
            ("inventory", "Inventory"), ("sales", "Sales (one row per sale)"),
            ("sale-items", "Sale line items"), ("customers", "Customers"), ("suppliers", "Suppliers"),
        ]
    )
    body = (
        brand_header("Export data")
        + "<p class='subtitle'>Module 20 -- download the current state of each table as CSV. "
          "For bulk import (suppliers, a TCG's catalogue), see the scripts/import_*_csv.py "
          "command-line tools instead -- see their own --help for the column format.</p>"
        + f"<div class='panel'><ul class='events'>{links}</ul></div>"
    )
    return HTMLResponse(page("EzBay — Export", body))


@router.get("/export/inventory.csv")
def export_inventory(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> Response:
    rows = session.execute(select(InventoryItem, CatalogItem).outerjoin(CatalogItem, InventoryItem.catalog_item_id == CatalogItem.id)).all()
    data = [
        [item.id, catalog.name if catalog else None, item.card_id, item.variant.value, item.condition,
         item.quantity, item.status.value, item.storage_location_id, item.grading_company,
         item.certification_number, item.added_at.isoformat()]
        for item, catalog in rows
    ]
    header = ["id", "catalog_item_name", "card_id", "variant", "condition", "quantity", "status",
              "storage_location_id", "grading_company", "certification_number", "added_at"]
    return _csv_response("inventory.csv", header, data)


@router.get("/export/sales.csv")
def export_sales(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> Response:
    sales = session.scalars(select(Sale).order_by(Sale.sold_at)).all()
    header = ["id", "sold_at", "customer_id", "marketplace_id", "gross_amount", "fees_amount",
              "shipping_revenue", "shipping_cost", "packaging_cost", "tax_amount", "notes"]
    data = [
        [s.id, s.sold_at.isoformat(), s.customer_id, s.marketplace_id, s.gross_amount, s.fees_amount,
         s.shipping_revenue, s.shipping_cost, s.packaging_cost, s.tax_amount, s.notes]
        for s in sales
    ]
    return _csv_response("sales.csv", header, data)


@router.get("/export/sale-items.csv")
def export_sale_items(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> Response:
    items = session.scalars(select(SaleItem).order_by(SaleItem.sale_id)).all()
    header = ["id", "sale_id", "inventory_item_id", "description", "quantity", "unit_price", "cost_basis"]
    data = [[i.id, i.sale_id, i.inventory_item_id, i.description, i.quantity, i.unit_price, i.cost_basis] for i in items]
    return _csv_response("sale-items.csv", header, data)


@router.get("/export/customers.csv")
def export_customers(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> Response:
    customers = session.scalars(select(Customer).order_by(Customer.display_name)).all()
    header = ["id", "display_name", "segment", "notes", "created_at"]
    data = [[c.id, c.display_name, c.segment, c.notes, c.created_at.isoformat()] for c in customers]
    return _csv_response("customers.csv", header, data)


@router.get("/export/suppliers.csv")
def export_suppliers(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> Response:
    suppliers = session.scalars(select(Supplier).order_by(Supplier.name)).all()
    header = ["id", "name", "contact", "website", "categories", "account_status",
              "wholesale_discount_pct", "minimum_order"]
    data = [
        [s.id, s.name, s.contact, s.website, s.categories, s.account_status.value,
         s.wholesale_discount_pct, s.minimum_order]
        for s in suppliers
    ]
    return _csv_response("suppliers.csv", header, data)
