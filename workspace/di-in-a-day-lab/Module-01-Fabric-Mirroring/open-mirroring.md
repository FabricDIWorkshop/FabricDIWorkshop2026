# Module 04 - Open Mirroring in Microsoft Fabric

## Lab overview

In this lab, you create an **Open Mirrored Database** in Microsoft Fabric and land files into its OneLake landing zone. Open Mirroring watches the landing zone, creates mirrored Delta tables, and applies both initial history and incremental changes.

You will generate data for two tables:

- `Customers`
- `Orders`

The lab writes the data in two phases:

1. **History load** - first full snapshot for both tables.
2. **Incremental load** - a second file for both tables containing one insert, one update, and one delete.

The notebook first saves all files into the Lakehouse **Files** section so participants can inspect them. Then it copies the history files, and later the incremental files, into the Open Mirroring landing zone.

## Learning objectives

By the end of this module, you will be able to:

- Create an Open Mirrored Database item in Fabric.
- Locate the Open Mirroring landing zone URL.
- Generate `_metadata.json` files with primary key definitions.
- Generate sequential Parquet files for initial history and incremental changes.
- Use `__rowMarker__` values to represent insert, update, and delete events.
- Copy files from Lakehouse Files into the Open Mirroring landing zone.
- Monitor Open Mirroring replication and validate results.

## Prerequisites

- A Fabric workspace backed by an active Fabric capacity.
- Permission to create items in that workspace.
- A Lakehouse attached to the notebook. The notebook uses the Lakehouse **Files** section as the staging area.
- The uploadable notebook: `Fabric_Open_Mirroring_Lab.ipynb`.

## Key Open Mirroring concepts

### Landing zone

Every Open Mirrored Database has a landing zone in OneLake:

```text
<Open mirrored database>/Files/LandingZone
```

The notebook copies staged files into this location.

### Table folders

Each mirrored table has its own folder under the landing zone:

```text
Files/LandingZone/Customers
Files/LandingZone/Orders
```

### Metadata file

Each table folder needs a `_metadata.json` file that defines the table key columns.

`Customers/_metadata.json`:

```json
{
  "keyColumns": ["CustomerID"]
}
```

`Orders/_metadata.json`:

```json
{
  "keyColumns": ["OrderID"]
}
```

If `_metadata.json` or `keyColumns` is missing, Open Mirroring cannot apply updates and deletes correctly.

### File naming

Open Mirroring expects monotonically increasing 20-digit file names:

```text
00000000000000000001.parquet
00000000000000000002.parquet
```

In this lab:

- `00000000000000000001.parquet` is the history file.
- `00000000000000000002.parquet` is the incremental change file.

### Row marker values

Incremental files include a final column named `__rowMarker__`.

| `__rowMarker__` | Meaning |
| --- | --- |
| `0` | Insert |
| `1` | Update |
| `2` | Delete |
| `4` | Upsert |

The initial history files in this lab do **not** include `__rowMarker__`. Open Mirroring treats initial files without `__rowMarker__` as inserts.

## Lab architecture

```text
Fabric Notebook
  |
  | creates files
  v
Lakehouse Files section
  Files/open-mirroring-lab/history/Customers/...
  Files/open-mirroring-lab/history/Orders/...
  Files/open-mirroring-lab/incremental/Customers/...
  Files/open-mirroring-lab/incremental/Orders/...
  |
  | copies files
  v
Open Mirrored Database landing zone
  Files/LandingZone/Customers
  Files/LandingZone/Orders
  |
  | Open Mirroring processes files
  v
Mirrored Delta tables in OneLake
  Customers
  Orders
```

## Part 1 - Create the Open Mirrored Database

1. Open the Fabric workspace where you want to run the lab.
2. Select **New item**.
3. Search for and select **Mirrored Database**.
4. Create a new mirrored database named `OpenMirroringLab`.
5. Open the new mirrored database item.
6. On the Home page, locate the **Landing zone URL**.
7. Copy the landing zone URL; you will paste it into the notebook configuration cell.

## Part 2 - Attach a Lakehouse to the notebook

1. Upload `Fabric_Open_Mirroring_Lab.ipynb` into the same workspace.
2. Open the notebook.
3. Attach a Lakehouse when prompted.
4. If no Lakehouse exists, create one in the workspace and attach it.

The notebook writes all generated data under:

```text
Files/open-mirroring-lab
```

This lets you inspect the generated history and incremental files before they are copied to the Open Mirroring landing zone.

## Part 3 - Generate history and incremental files

Run the notebook cells through **Generate Open Mirroring files in Lakehouse Files**.

The notebook creates:

```text
Files/open-mirroring-lab/history/Customers/_metadata.json
Files/open-mirroring-lab/history/Customers/00000000000000000001.parquet
Files/open-mirroring-lab/history/Orders/_metadata.json
Files/open-mirroring-lab/history/Orders/00000000000000000001.parquet
Files/open-mirroring-lab/incremental/Customers/00000000000000000002.parquet
Files/open-mirroring-lab/incremental/Orders/00000000000000000002.parquet
```

### History data

`Customers` history:

| CustomerID | CustomerName | City | Status | LastUpdated |
| --- | --- | --- | --- | --- |
| C001 | Contoso Retail | Seattle | Active | 2026-08-01T09:00:00Z |
| C002 | Fabrikam Labs | Portland | Active | 2026-08-01T09:05:00Z |
| C003 | Adventure Works | Vancouver | Active | 2026-08-01T09:10:00Z |

`Orders` history:

| OrderID | CustomerID | Product | Quantity | Amount | OrderStatus | LastUpdated |
| --- | --- | --- | ---: | ---: | --- | --- |
| O1001 | C001 | Widget | 2 | 199.90 | Open | 2026-08-01T10:00:00Z |
| O1002 | C002 | Laptop | 1 | 1299.00 | Open | 2026-08-01T10:05:00Z |
| O1003 | C003 | Support Plan | 1 | 499.00 | Open | 2026-08-01T10:10:00Z |

## Part 4 - Land history files into Open Mirroring

Run the notebook cell **Copy history files to Open Mirroring landing zone**.

The notebook copies `_metadata.json` and `00000000000000000001.parquet` into each table folder in the Open Mirroring landing zone.

Then return to the Open Mirrored Database item in Fabric:

1. Select **Mirror database** or open the mirroring configuration screen.
2. Leave **Mirror all data** selected.
3. Start mirroring.
4. Select **Monitor replication**.
5. Wait until `Customers` and `Orders` show as running and refreshed.

## Part 5 - Validate history load

Use either the notebook validation cell or the SQL analytics endpoint.

Expected history result:

- `Customers` has 3 rows.
- `Orders` has 3 rows.

## Part 6 - Land incremental files

Run the notebook cell **Copy incremental change files to Open Mirroring landing zone**.

The incremental file for each table includes:

- one insert,
- one update,
- one delete.

### Customers incremental changes

| Change | CustomerID | Result |
| --- | --- | --- |
| Update | C002 | City changes from Portland to Bellevue |
| Insert | C004 | Northwind Traders is added |
| Delete | C003 | Adventure Works is removed |

### Orders incremental changes

| Change | OrderID | Result |
| --- | --- | --- |
| Update | O1002 | Status changes from Open to Shipped |
| Insert | O1004 | New Cloud Backup order is added |
| Delete | O1003 | Support Plan order is removed |

## Part 7 - Monitor and validate incremental replication

In the Open Mirrored Database item:

1. Open **Monitor replication**.
2. Refresh until `Customers` and `Orders` show a new **Last refresh** timestamp.
3. Run the notebook validation cell.

Expected final state:

`Customers`:

| CustomerID | CustomerName | City | Status |
| --- | --- | --- | --- |
| C001 | Contoso Retail | Seattle | Active |
| C002 | Fabrikam Labs | Bellevue | Active |
| C004 | Northwind Traders | Redmond | Active |

`Orders`:

| OrderID | CustomerID | Product | Quantity | Amount | OrderStatus |
| --- | --- | --- | ---: | ---: | --- |
| O1001 | C001 | Widget | 2 | 199.90 | Open |
| O1002 | C002 | Laptop | 1 | 1299.00 | Shipped |
| O1004 | C004 | Cloud Backup | 3 | 299.97 | Open |

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Open Mirrored Database does not show tables | Confirm `_metadata.json` and the history Parquet file are in each table folder under `Files/LandingZone`. |
| Incremental changes do not apply | Confirm the incremental file is named `00000000000000000002.parquet` and includes `__rowMarker__` as the final column. |
| Updates/deletes do not work | Confirm `keyColumns` exists in `_metadata.json` and matches the key column in the data. |
| Notebook cannot copy files to landing zone | Confirm the landing zone URL is correct and your account has access to the mirrored database item. |
| Tables stop replicating after a schema change | Open Mirroring does not support direct type changes. Recreate the table folder if schema changes are required. |

## Reference

- [Tutorial: Configure Microsoft Fabric open mirrored databases](https://learn.microsoft.com/en-us/fabric/mirroring/open-mirroring-tutorial)
- [Open Mirroring landing zone requirements and format](https://learn.microsoft.com/en-us/fabric/mirroring/open-mirroring-landing-zone-format)
