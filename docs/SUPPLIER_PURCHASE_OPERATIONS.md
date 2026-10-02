# Supplier purchase and receiving operations

Open **Research → Market Tools → Suppliers**. Supplier and purchase-lot screens
are exposed now because they write through to the canonical catalogue and
inventory; they are no longer isolated reference forms.

## Set up a supplier

1. Add the supplier with its contact, website, categories and account status.
2. Open the supplier, search for the exact canonical catalogue product, then add
   its current quoted unit cost and minimum order quantity. The same catalogue
   item cannot be linked twice to one supplier.
3. If the product is not in the catalogue, add or approve it through the normal
   stock/catalogue workflow first. Do not create a near-duplicate supplier-only
   product name.

## Create and receive a purchase lot

1. From the supplier page choose **Create a purchase lot from this supplier**.
2. Enter the source/order description, asking total and target buy percentage.
3. Add the supplier products, quantities, condition and expected market value.
   The supplier quote is snapshotted on each line, so a later supplier price
   change does not rewrite the purchase decision.
4. Record the final offered/purchase price and select **Purchased** only when the
   order should be received into EzBay inventory.

On receipt, EZBay uses each line's quoted extended cost as its allocation weight
(falling back to market value for non-supplier lots). It distributes the exact
final lot price across every line, lets the final line absorb cent rounding, and
stores both allocated line total and allocated unit cost. Quantities merge into
the matching catalogue/variant/condition holding using a weighted unit basis.

Receipt is one-time and locks the lot's cost and inventory links. A repeated
request cannot add stock twice. Retailer-generated lots are also read-only here;
Retailer Goals owns their synchronized refund and fund-ledger lifecycle.

## Non-destructive rehearsal

Run:

```bash
.venv/bin/python -m scripts.supplier_purchase_drill
```

The drill uses a temporary database and makes no supplier, payment, marketplace
or other external calls. It covers supplier creation, catalogue linking, quoted
cost snapshots, purchase-lot creation, proportional cost allocation, canonical
inventory receipt and duplicate-receipt protection.
