# Module 06 — Chat with Mirrored Data

Build a Microsoft Fabric data agent that answers natural-language questions about the AdventureWorksLT data mirrored in Module 01. Configure its data source, provide business instructions, teach it with question-and-SQL examples, and validate its answers before publishing.

This workshop adapts the [Microsoft Learn end-to-end data agent tutorial](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-end-to-end-tutorial). That tutorial loads an AdventureWorks lakehouse; this module reuses your **mirrored database**, so no additional lakehouse or data load is needed. The examples below use the workshop's `SalesLT` tables, not the tutorial's dimensional tables.

**Estimated time:** 30–45 minutes.

## Learning objectives

- Connect a data agent to a mirrored database and select relevant tables.
- Explain business definitions and relationships through instructions.
- Add example question/query pairs to guide SQL generation.
- Compare answers with the SQL analytics endpoint, refine the agent, and publish it.

## Prerequisites

- Complete [Module 01 — Fabric Mirroring](../Module-01-Fabric-Mirroring/fabric-mirroring-azure-sql-lab.md). Replication must be running and the `SalesLT` tables queryable in the mirrored item **`AdventureWorksLT_Mirrored`**.
- Use your workshop workspace, `<your-workspace>` (for example, `DIWORKSPACE_diuser1`). Check [workshop_environment.md](../../../workshop_environment.md) for deployment-specific values.
- The workspace must use an active, supported paid Fabric capacity (F2 or higher), or Power BI Premium capacity (P1 or higher) with Fabric enabled, as described in the tutorial.
- A Fabric administrator must enable the required [data agent tenant settings](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-tenant-settings), including cross-geo AI processing/storage settings where required. General mirroring settings alone do not enable data agents.
- You need permission to create workspace items and read the mirror's SQL analytics endpoint. Setup makes workshop participants workspace Admins.
- For use in standalone Copilot scenarios, the administrator must also enable **Standalone Copilot experience** under Copilot tenant settings. This is separate from testing in the data agent editor.

> **Source versus mirror:** `sqldb-di-workshop-diuser1` is an example **source Azure SQL database**. In this module, select the Fabric mirrored item **`AdventureWorksLT_Mirrored`**. Do not enter `sqladmin` credentials or create another Azure SQL connection.

## Create a Fabric data agent

1. Open [Microsoft Fabric](https://app.fabric.microsoft.com) and switch to your workshop workspace.
2. Select **New item** (or **+ New item**).
3. Search for **Fabric data agent** or **Data agent**, then select the item.
4. Enter `AdventureWorks Chat Agent` as the name.
5. Select **Create** to open the editor.

> Data agents are a preview feature in the referenced tutorial. Labels and pane placement can change as the Fabric UI evolves.

## Select the data

1. In the editor, select **Add data source**.
2. In the source picker, find your workspace and select **`AdventureWorksLT_Mirrored`**. Depending on your UI version, the picker may be labeled **OneLake catalog** or **OneLake data hub**.
3. Select **Add**.
4. In the left **Explorer** pane, expand the source to see its tables.
5. Use the table checkboxes to select these five tables:

   | Table | Purpose |
   | --- | --- |
   | `SalesLT.Customer` | Customer identifiers and company information |
   | `SalesLT.SalesOrderHeader` | Order date and the customer who placed the order |
   | `SalesLT.SalesOrderDetail` | Product, quantity, price, and discount for each order line |
   | `SalesLT.Product` | Product identifiers, product numbers, and category identifiers |
   | `SalesLT.ProductCategory` | Category identifiers and parent-category relationships |

6. Confirm the selected tables remain checked before continuing.

> **Available mirrored columns:** In this workshop's mirrored schema, `Customer` does not expose `FirstName` or `LastName`, and neither `Product` nor `ProductCategory` exposes `Name`. Use `CustomerID` and `CompanyName` for customers, `ProductID` and `ProductNumber` for products, and `ProductCategoryID` for categories. The examples below use only the columns available in the supplied schema.

Start with the tables needed for this exercise rather than selecting every table. A focused schema reduces irrelevant context and makes joins easier to review. Add address or description tables later if your questions need them.

### Ask a baseline question

1. In the chat pane, ask: **Show me the top 10 customers by total sales amount.**
2. Expand the generated query or response details, if available.
3. Note the tables, joins, and sales calculation. Keep this result for comparison after adding instructions and examples.

Do not treat a fluent answer as proof that the calculation is correct. The next steps define what “sales amount” means in this workshop.

## Provide instructions

Instructions tell the agent how to interpret the dataset and your business terminology. For example, “sales” could mean order totals including tax and freight, or discounted line revenue excluding both. Explicit guidance reduces this ambiguity.

1. In the left **Explorer** pane, select the **Setup** tab, then select **Agent instructions** to open the instructions editor.
2. Enter this workshop-specific guidance, replacing the source name if you used a different mirrored item name:

   ```text
   Purpose and source
   Answer questions about AdventureWorksLT customers, products, and sales using
   AdventureWorksLT_Mirrored and its selected SalesLT tables. Use only tables and
   columns exposed by this source. Do not invent missing fields or results.
   Identify customers by CustomerID and CompanyName; FirstName and LastName
   are not available. Identify products by ProductID and ProductNumber, and
   categories by ProductCategoryID; Product.Name and ProductCategory.Name are
   not available. Do not invent product or category labels.

   Relationships
   Join Customer to SalesOrderHeader on CustomerID.
   Join SalesOrderHeader to SalesOrderDetail on SalesOrderID.
   Join SalesOrderDetail to Product on ProductID.
   Join Product to ProductCategory on ProductCategoryID.
   Customer and SalesOrderDetail do not have a direct customer-key relationship.

   Business definitions
   Unless explicitly requested otherwise, sales amount and revenue mean
   SUM(OrderQty * UnitPrice * (1 - UnitPriceDiscount)) from order lines.
   UnitPriceDiscount is a fraction, not a whole-number percentage.
   This revenue excludes tax and freight. Explain the definition in the answer.
   Do not rely on SalesOrderDetail.LineTotal: that computed column is not
   replicated in this workshop. Calculate discounted line revenue explicitly.
   Use SalesOrderHeader.OrderDate for monthly or yearly sales questions.
   Average order quantity per product category means AVG(OrderQty) across order
   lines, not average units per complete order. State this interpretation.

   Query and answer behavior
   Use read-only T-SQL. Do not insert, update, delete, or change schema.
   Avoid counting an order header's total once per joined order line.
   For customer rankings, group by CustomerID and CompanyName.
   Return concise tables with descriptive column names and the requested sort.
   For ambiguous requests, ask for clarification or state the assumption.
   Sample data is historical: do not default to the current year unless asked.
   If a period has no matching data, say so; do not make up values.
   Mirrored data can lag behind the source. Do not claim it is instantaneous.
   ```

3. Instructions are saved automatically. Confirm the editor shows the changes as saved before continuing.
4. Ask the baseline question again, preferably in a new conversation if the editor offers that option.
5. Check whether the answer explains discounted line revenue and joins customers through `SalesOrderHeader`.

### How instructions help

| Guidance | Why it matters |
| --- | --- |
| Explicit relationships | Helps prevent an incorrect direct join between customers and order details. |
| A shared revenue definition | Makes answers comparable and avoids silently including tax or freight. |
| A formula instead of `LineTotal` | Avoids referencing a computed column missing from the mirror. |
| A defined aggregation grain | Distinguishes average line quantity from average quantity per order. |
| Historical-data and ambiguity guidance | Reduces misleading empty current-year answers and unstated assumptions. |

Instructions guide generation; they do **not** enforce security, guarantee correct SQL, or replace validation. Read-only behavior and access boundaries must also be enforced by the data source and the user's permissions.

## Provide examples

Examples pair a natural-language question with correct SQL for your schema. Instructions explain the rules; examples demonstrate how to apply them through joins, calculations, grouping, and ordering. Use a few representative, validated examples rather than many near-duplicates.

1. Open `AdventureWorksLT_Mirrored` in another tab and switch to its **SQL analytics endpoint**.
2. Run each SQL example below there first. Confirm the tables and columns exist and inspect the results.
3. Return to the agent editor. In the left **Explorer** pane, select the **Setup** tab.
4. Expand **AdventureWorksLT_Mirrored**, then select **Example queries** beneath that data source.
5. In the example queries pane, select **+ Add**, then enter a question and its matching SQL in the corresponding fields. Repeat for each example below.
6. Save or apply the examples, and confirm the changes are saved.

> These examples target the mirror's SQL analytics endpoint. The tutorial notes that question/query example pairs are not currently supported for Power BI semantic model sources; this lab does not use a semantic model.

### Example 1 — Top customers by revenue

**Question:** Show me the top 10 customers by total sales amount, excluding tax and freight.

```sql
SELECT TOP (10)
	c.CustomerID,
	c.CompanyName,
	SUM(d.OrderQty * d.UnitPrice * (1 - d.UnitPriceDiscount)) AS SalesAmount
FROM SalesLT.Customer AS c
INNER JOIN SalesLT.SalesOrderHeader AS h ON h.CustomerID = c.CustomerID
INNER JOIN SalesLT.SalesOrderDetail AS d ON d.SalesOrderID = h.SalesOrderID
GROUP BY c.CustomerID, c.CompanyName
ORDER BY SalesAmount DESC, c.CustomerID;
```

**What it teaches:** Customers relate to order details through the order header. Revenue comes from discounted lines, and grouping includes `CustomerID` so customers with the same company name are not merged. No unavailable customer-name columns are referenced.

### Example 2 — Average order-line quantity by category

**Question:** What is the average order quantity per product category? Use the average quantity per order line.

```sql
SELECT
	pc.ProductCategoryID,
	AVG(CAST(d.OrderQty AS decimal(18, 2))) AS AverageLineQuantity
FROM SalesLT.SalesOrderDetail AS d
INNER JOIN SalesLT.Product AS p ON p.ProductID = d.ProductID
INNER JOIN SalesLT.ProductCategory AS pc
	ON pc.ProductCategoryID = p.ProductCategoryID
GROUP BY pc.ProductCategoryID
ORDER BY AverageLineQuantity DESC, pc.ProductCategoryID;
```

**What it teaches:** Follow the product-to-category relationship and cast quantity to a decimal before averaging to retain fractional results. Categories are identified by `ProductCategoryID` because a category-name column is not available. This groups by the product's assigned category, not by a rolled-up parent category.

### Example 3 — Monthly and year-to-date revenue

**Question:** Show monthly sales and cumulative year-to-date sales for every year available in the data, ordered by year and month. Exclude tax and freight.

```sql
WITH MonthlySales AS (
	SELECT
		YEAR(h.OrderDate) AS SalesYear,
		MONTH(h.OrderDate) AS SalesMonth,
		SUM(d.OrderQty * d.UnitPrice * (1 - d.UnitPriceDiscount)) AS SalesAmount
	FROM SalesLT.SalesOrderHeader AS h
	INNER JOIN SalesLT.SalesOrderDetail AS d ON d.SalesOrderID = h.SalesOrderID
	GROUP BY YEAR(h.OrderDate), MONTH(h.OrderDate)
)
SELECT
	SalesYear,
	SalesMonth,
	SalesAmount AS MonthlySalesAmount,
	SUM(SalesAmount) OVER (
		PARTITION BY SalesYear
		ORDER BY SalesMonth
		ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
	) AS YearToDateSalesAmount
FROM MonthlySales
ORDER BY SalesYear, SalesMonth;
```

**What it teaches:** Aggregate by order date before applying a cumulative window, and reset the running total for each year. Only months with orders appear; the sample may contain a limited date range, so do not expect twelve rows per year.

## Test and revise the Fabric data agent

1. Ask the three example questions in the agent chat pane.
2. Review generated SQL or query details, not only the written explanation.
3. Compare each answer with the results you obtained directly from the SQL analytics endpoint. Display-rounding differences can occur; unexplained differences in totals, grouping, or row counts need investigation.
4. Ask related questions that are **not** exact saved examples:
   - **Show the top five products by discounted sales amount, with ProductID and ProductNumber.**
   - **For those products, also show the total units sold.**
   - **Which order dates are present in the data?**
5. Try an ambiguous question: **Which customers are the best?** Check that the agent asks what “best” means or states the measure it chooses.

### Validation checklist

- Customer revenue uses `Customer` → `SalesOrderHeader` → `SalesOrderDetail`.
- Category queries use `SalesOrderDetail` → `Product` → `ProductCategory`.
- Customers are identified by `CustomerID` and `CompanyName`; products by `ProductID` and `ProductNumber`; categories by `ProductCategoryID`. No query references unavailable `FirstName`, `LastName`, or product/category `Name` columns.
- Revenue uses discounted order-line prices, not the product's current `ListPrice`.
- No query references the missing `SalesOrderDetail.LineTotal` column.
- Joined order headers are not double-counted when summing header-level amounts.
- Date filters match the question and dates actually present in the sample.
- Follow-up answers retain the intended measure and explain material assumptions.

Record one before-and-after observation from your baseline question: what changed after adding instructions or examples, and how did you verify the improvement?

## Publish the Fabric data agent

1. When validation checks pass, select **Publish** in the editor.
2. Review the **Publish data agent** dialog and select **Publish** to confirm.
3. Open **Settings** to inspect the published details or URL available in your UI.
4. If you change instructions, examples, or selected sources later, test and publish the updated version before consumers rely on it.


**Optional extension:** The Microsoft Learn tutorial also covers programmatic consumption of a published data agent through its MCP endpoint. That is outside this portal-based exercise; use the current tutorial for authentication and client setup rather than adding Azure SQL credentials to an application.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Data agent is unavailable or cannot be created | Confirm supported active capacity, required tenant AI settings, feature availability, and workspace permissions with the facilitator. |
| Mirrored source is missing | Confirm the workspace, item name `AdventureWorksLT_Mirrored`, and read access. Do not search only for the source Azure SQL database name. |
| Tables are missing or queries return no data | Check Module 01 replication status and query the mirror's SQL analytics endpoint directly. Confirm table checkboxes are selected in the agent. |
| SQL uses `LineTotal` or joins customers directly to order details | Revisit the instructions, save the customer-revenue example, and retest. |
| SQL uses `Customer.FirstName`, `Customer.LastName`, `Product.Name`, or `ProductCategory.Name` | These columns are not available in the supplied mirrored schema. Use the identifiers and company/product-number fields specified in the instructions, update saved examples, and retest. |
| Revenue looks inflated | Check for repeated header totals after joining order lines, incorrect join keys, or a mismatch in tax/freight treatment. |
| A current-year question returns no rows | Check actual order dates; historical AdventureWorksLT data may have no orders in the current year. |
| Published behavior differs from the editor | Confirm the latest changes were published and consumers have the necessary data permissions. |

## Key takeaways

- Mirroring supplies a continuously replicated, read-only analytical copy; the data agent adds a natural-language interface over it.
- Instructions establish business meaning and expected behavior; examples demonstrate SQL patterns for this schema.
- Plausible answers still need validation against the mirrored data and its SQL analytics endpoint.
- Publishing makes a validated version available for consumption; it does not bypass permissions or eliminate replication lag.

## Reference

- [Microsoft Learn — Fabric data agent end-to-end tutorial](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-end-to-end-tutorial)
- [Microsoft Learn — Fabric data agent tenant settings](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-tenant-settings)
