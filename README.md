# Fabric Data Integration in a Day Lab

<!-- Banner placeholder – replace with your own workshop graphic -->
<!-- ![DI in a Day Banner](assets/images/di-in-a-day-banner.png) -->

A hands-on workshop that walks Partners, Customers, and Internal Insiders through the full **Data Integration** stack in **Microsoft Fabric**. Participants will move data using every major Fabric ingestion method, orchestrate workflows with low-code pipelines and code-first Apache Airflow, transform data with visual and notebook-based tools, and monitor everything from a single pane of glass.

> **Audience:** Partners · Customers · Internal Insiders  
> **Duration:** ~6 hours (full-day)  
> **Level:** Intermediate (200–300)

---

## Agenda (approx.)

> [!IMPORTANT]
> Times are approximate. Facilitators may adjust based on group pace and discussion.

| Time | Duration | Session |
|---|---|---|
| 00:00 – 00:15 | 15 min | **Kickoff, Goals & Lab Installation** |
| 00:15 – 01:15 | 60 min | **Module 1 — Fabric Mirroring** (Azure SQL Mirroring · Open Mirroring) |
| 01:15 – 01:30 | 15 min | Break |
| 01:30 – 02:30 | 60 min | **Module 2 — Data Movement** (Copy Job · Copy Activity · Eventstream) |
| 02:30 – 02:45 | 15 min | Break |
| 02:45 – 03:45 | 60 min | **Module 3 — Orchestration** (Pipeline · Apache Airflow) |
| 03:45 – 04:00 | 15 min | Break |
| 04:00 – 05:00 | 60 min | **Module 4 — Transformation** (Mapping Data Transform in DF Gen2 · Dataflow Gen2 · Notebook · TSQL/Stored Procedure) |
| 05:00 – 05:15 | 15 min | Break |
| 05:15 – 06:15 | 60 min | **Module 5 — Monitoring & Telemetry** |
| 06:15 – 06:30 | 15 min | Break |
| 06:30 – 07:30 | 60 min | **Module 6 — Fabric IQ: From Data to Insights** |
| 07:30 – 07:45 | 15 min | **Wrap-up & Q&A** |

---

## Prerequisites

| Requirement | Details |
|---|---|
| **Microsoft Fabric Capacity** | F64 or higher (or a Fabric Trial capacity) |
| **Fabric Workspace** | A workspace assigned to the above capacity |
| **Permissions** | Workspace Admin or Member role |
| **Browser** | Microsoft Edge or Google Chrome (latest) |
| **Optional** | VS Code with Python ≥ 3.10 for the Airflow / Notebook exercises |

---

## Install the Lab in Fabric

### Step 1 — Create a Fabric Workspace

1. Navigate to [app.fabric.microsoft.com](https://app.fabric.microsoft.com).
2. Select **Workspaces** → **+ New workspace**.
3. Name it `DI-in-a-Day-Lab`.
4. Under **Advanced**, assign it to your Fabric capacity.

### Step 2 — Provision Sample Data Sources

The lab uses the following sample data. Facilitators should provision these **before** the session:

| Source | Type | Purpose |
|---|---|---|
| **Azure SQL Database** | `AdventureWorksLT` | Relational source for Copy Job, Copy Activity, and Mirroring exercises |
| **Azure Event Hubs** (or Fabric Eventstream sample) | Streaming | Real-time ingestion exercise |
| **Azure Blob Storage / ADLS Gen2** | Parquet & CSV files | File-based ingestion for Copy Activity and Dataflow Gen2 |

> [!NOTE]
> Detailed provisioning scripts are available in the [`workspace/di-in-a-day-lab/setup/`](workspace/di-in-a-day-lab/setup/) folder.

### Step 3 — Import Starter Artifacts (Optional)

If pre-built notebooks and pipelines are provided, import them into the workspace:
1. In your workspace select **Import** → **Notebook** (or **Pipeline**).
2. Upload the `.ipynb` / `.json` files from the `workspace/di-in-a-day-lab/` folder.

---

## What You Will Build

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             DATA SOURCES                                    │
│   Azure SQL DB · ADLS Gen2 · Event Hubs · On-prem SQL Server                │
└──────┬──────────────┬──────────────┬──────────────┬─────────────────────────┘
       │              │              │              │
┌──────▼──────┐ ┌─────▼──────┐ ┌────▼─────┐ ┌─────▼───────────┐
│  Copy Job   │ │    Copy    │ │  Event   │ │   Mirroring     │
│ (Full &     │ │  Activity  │ │  stream  │ │ (Azure SQL /    │
│  Incremental│ │            │ │(Real-time│ │  Open Mirroring) │
│     /CDC)   │ │            │ │ingestion)│ │  [Module 1]     │
└──────┬──────┘ └─────┬──────┘ └────┬─────┘ └─────┬───────────┘
       │              │              │              │
       └──────────────┴──────┬───────┴──────────────┘
                             │
                ┌────────────▼─────────────┐
                │     Bronze / Landing     │
                │   (Lakehouse / OneLake)  │
                └────────────┬─────────────┘
                             │
       ┌─────────────────────┼─────────────────────────────────┐
       │                     │                  │              │
┌──────▼──────────┐ ┌────────▼───────┐ ┌───────▼───────┐ ┌────▼──────────────┐
│ Mapping Data    │ │   Dataflow     │ │   Notebook    │ │ TSQL / Stored     │
│ Transform in    │ │   Gen2         │ │  (PySpark /   │ │ Procedure         │
│ DF Gen2         │ │  (Code-free    │ │   Spark SQL)  │ │ (Warehouse /      │
│ (Visual ETL)    │ │   Data Prep)   │ │               │ │  SQL Endpoint)    │
└──────┬──────────┘ └────────┬───────┘ └───────┬───────┘ └────┬──────────────┘
       │                     │                  │              │
       └─────────────────────┼──────────────────┴──────────────┘
                             │
                ┌────────────▼─────────────┐
                │     Silver / Gold        │
                │  (Lakehouse / Warehouse) │
                └────────────┬─────────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                                     │
┌─────────▼──────────┐            ┌─────────────▼──────────┐
│  Pipeline          │            │  Apache Airflow        │
│  (Low-code         │            │  (Code-first           │
│   Orchestration)   │            │   Orchestration)       │
└─────────┬──────────┘            └─────────────┬──────────┘
          │                                     │
          └──────────────────┬──────────────────┘
                             │
                ┌────────────▼─────────────┐
                │   Monitoring Hub &       │
                │   Telemetry              │
                └────────────┬─────────────┘
                             │
                ┌────────────▼─────────────┐
                │   Fabric IQ              │
                │   From Data to Insights  │
                │  (Natural Language →     │
                │   SQL · Reports · KPIs)  │
                └──────────────────────────┘
```

---

## Scenario

**Contoso Retail Inc.** is a global retailer that operates across multiple regions. The company has data scattered across Azure SQL databases, on-premises SQL Servers, cloud storage (ADLS Gen2), and real-time point-of-sale event streams. The Data Engineering team has been tasked with:

1. **Consolidating** all data into Microsoft Fabric's OneLake.
2. **Transforming** raw data into analytics-ready gold tables.
3. **Orchestrating** end-to-end data pipelines — both visually and programmatically.
4. **Monitoring** pipeline health, performance, and data quality in real time.

You will play the role of a Contoso data engineer, building out this entire integration layer during the workshop.

---

## Lab Flow

1. **Kickoff & Setup** — Provision the workspace, connect to sample data sources, and verify connectivity.
2. **Fabric Mirroring (Module 1)** — Set up Azure SQL Database mirroring and Open Mirroring for near real-time replication into OneLake.
3. **Move data (Module 2)** — Ingest historical and incremental data using Copy Job (full + incremental), Copy Activity (pipeline-based), and Eventstream (real-time streaming).
4. **Orchestrate (Module 3)** — Build a low-code pipeline to chain Copy, Transform, and Load activities. Then author an Apache Airflow DAG for code-first orchestration of the same workflow.
5. **Transform (Module 4)** — Shape and cleanse data using Mapping Data Transform in DF Gen2 (visual ETL), Dataflow Gen2 (Power Query-based code-free data prep), Fabric Notebook (PySpark / Spark SQL), and TSQL/Stored Procedures.
6. **Monitor (Module 5)** — Use the Monitoring Hub, activity history, schedule failure notifications, and capacity metrics to observe pipeline execution and troubleshoot issues.
7. **Fabric IQ (Module 6)** — Use natural language to query data, generate reports, and derive insights from your integrated data estate.
8. **Wrap-up** — Review the decision guide, discuss trade-offs, and Q&A.

---

## Skills Practiced

- **Fabric Mirroring** — Azure SQL Database mirroring, Open Mirroring, near real-time replication to OneLake.
- **Data Movement** — Copy Job (full & incremental/CDC), Copy Activity in pipelines, Eventstream real-time ingestion.
- **Orchestration** — Fabric Data Factory Pipeline (low-code canvas), Apache Airflow DAGs (code-first Python).
- **Transformation** — Mapping Data Transform in DF Gen2 (visual ETL), Dataflow Gen2 (300+ Power Query transforms), Fabric Notebook (PySpark, Spark SQL), TSQL/Stored Procedures.
- **Monitoring & Telemetry** — Monitoring Hub, activity details & history, schedule failure alerts, capacity metrics.
- **Fabric IQ** — Natural language queries, AI-generated reports, data insights from integrated data estate.
- **Architecture** — Medallion architecture (Bronze → Silver → Gold), ETL vs. ELT decision-making, choosing the right Fabric integration tool.

---

## Modules

| Module | Title | Document |
|---|---|---|
| **Kickoff** | Goals, Prerequisites & Lab Installation | This README |
| **1** | Fabric Mirroring | [Module-01-Fabric-Mirroring/](workspace/di-in-a-day-lab/Module-01-Fabric-Mirroring/) |
| **2** | Data Movement | [Module-02-Data-Movement/](workspace/di-in-a-day-lab/Module-02-Data-Movement/) |
| **3** | Orchestration | [Module-03-Orchestration.md](workspace/di-in-a-day-lab/Module-03-Orchestration/Module-03-Orchestration.md) |
| **4** | Transformation | [Module-04-Transformation/](workspace/di-in-a-day-lab/Module-04-Transformation/Module-04-Transformation.md) |
| **5** | Monitoring & Telemetry | [Module-05-Monitoring-Telemetry.md](workspace/di-in-a-day-lab/Module-05-Monitoring-Telemetry.md) |
| **6** | Fabric IQ: From Data to Insights | *Coming soon* |

---

## Decision Guide Reference

This workshop is informed by the **Microsoft Fabric Decision Guide** for data movement and transformation. Use the following table when choosing the right tool:

| Capability | Copy Activity | Copy Job | Dataflow Gen2 | Eventstream | Spark (Notebook) |
|---|---|---|---|---|---|
| **Primary Persona** | Data Engineer | Business Analyst / Data Engineer | Data Engineer / Business Analyst | Data Engineer | Data Engineer |
| **Code Style** | No code / Low code | No code / Low code | No code / Low code | No code / Low code | Code (Python, Scala, SQL) |
| **Data Volume** | Low → High | Low → High | Low → High | Medium → High | Low → High |
| **Transformation** | Lightweight | Lightweight | 300+ functions | Lightweight | Full Spark libraries |
| **Sources** | 50+ connectors | 50+ connectors | 150+ connectors | CDC, Kafka, Event Hubs | Hundreds of Spark libs |
| **Interface** | Wizard / Canvas | Wizard / Canvas | Power Query | Canvas | Notebook / Spark Job Def |

> **Reference:** [Microsoft Fabric decision guide: copy activity, Copy job, dataflow, Eventstream, or Spark](https://learn.microsoft.com/en-us/fabric/fundamentals/decision-guide-pipeline-dataflow-spark)

---

## Contributing

This project welcomes contributions and suggestions. Please open an issue or submit a pull request.

## License

This project is licensed under the [MIT License](LICENSE).

## Code of Conduct

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
