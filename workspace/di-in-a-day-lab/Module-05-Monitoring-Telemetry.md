# Module 4 — Monitoring & Telemetry

| | |
|---|---|
| **Duration** | 30 minutes |
| **Objective** | Monitor pipeline execution, troubleshoot failures, configure alerts, and understand capacity consumption |
| **Skills** | Monitoring Hub · Activity History · Schedule Failure Notifications · Capacity Metrics · Spark Monitoring |

---

## Overview

Building data integration pipelines is only half the job. In production, you need to **monitor execution**, **detect failures early**, **troubleshoot root causes**, and **optimize performance**. Fabric provides a centralized **Monitoring Hub** that tracks every pipeline run, dataflow refresh, notebook execution, and Copy Job across all your workspaces.

### What Can You Monitor?

| Item Type | Tracked in Monitoring Hub |
|---|---|
| Copy Job | ✅ |
| Pipeline | ✅ |
| Dataflow Gen2 | ✅ |
| Notebook | ✅ |
| Spark Job Definition | ✅ |
| Lakehouse | ✅ |
| Semantic Model | ✅ |
| Experiment | ✅ |

---

## Exercise 1 — Exploring the Monitoring Hub

**Time:** ~10 minutes  
**Goal:** Navigate the Monitoring Hub, understand the activity table, and investigate a pipeline run.

### Step 1 — Open the Monitoring Hub

1. In the Fabric portal, select **Monitor** from the left navigation pane.
2. The **Activities** page displays up to 100 Fabric activities from the past 30 days, ordered by start time (newest first).

### Step 2 — Understand the Activity Table

The default table shows:

| Column | Description |
|---|---|
| **Name** | The Fabric item name (e.g., `PL_E2E_DataIntegration`) |
| **Type** | Item type (Pipeline, Dataflow Gen2, Notebook, Copy Job, etc.) |
| **Status** | Current run status: In Progress, Succeeded, Failed, Cancelled, Stopped |
| **Start time** | When the activity started |
| **Duration** | How long the activity ran |
| **Submitted by** | Who or what triggered the activity |
| **Location** | The workspace where the item resides |

### Step 3 — View Activity Details

1. Hover over any activity name → Select the **View details** icon (ℹ️).
2. The **Details pane** opens showing:
   - **Status**: Succeeded / Failed / In Progress
   - **Start time** and **End time**
   - **Duration**
   - **Error details** (if failed) — including error code and message
   - **Run ID** for API correlation

3. For **Pipeline** activities, you can also see:
   - Individual activity run statuses within the pipeline
   - Input/output data for each activity
   - Rows read / rows written for Copy activities

### Step 4 — Filter and Search

1. Use the **Filter by keyword** search box to find activities by name.
2. Select **Filter** to narrow results:

   | Filter | Options |
   |---|---|
   | **Status** | In Progress · Succeeded · Failed · Cancelled |
   | **Item type** | Pipeline · Dataflow Gen2 · Notebook · Copy Job · etc. |
   | **Start time** | Last 24 hours · Last 7 days · Last 30 days · Custom |
   | **Submitted by** | Select specific users |
   | **Location** | Select specific workspaces |

3. **Example investigation:** Find all failed pipelines in the last 24 hours:
   - Set **Status** = `Failed`
   - Set **Item type** = `Pipeline`
   - Set **Start time** = `Last 24 hours`
   - Review the filtered results

### Step 5 — View Activity History

The main Activities page shows only the 100 most recent activities. For frequently-run jobs, you need the full history:

1. Hover over an activity → Select **More options** (⋯) → **Historical runs**.
2. The table displays up to **30 days** of historical information for that specific activity.
3. Use this to:
   - Identify recurring failures
   - Compare execution durations over time
   - Validate that a fix resolved the issue
4. Select **Back to main view** to return.

> **✅ Checkpoint:** You can navigate the Monitoring Hub, filter activities, view details, and access historical runs.

---

## Exercise 2 — Investigating a Pipeline Failure

**Time:** ~5 minutes  
**Goal:** Deliberately trigger a failure and trace through the Monitoring Hub to find the root cause.

### Step 1 — Trigger a Failure

1. Open the pipeline `PL_E2E_DataIntegration` from Module 2.
2. Modify the Copy Activity source query to reference a **non-existent table**:

   ```sql
   SELECT * FROM SalesLT.NonExistentTable WHERE 1=1
   ```

3. Select **Run** to execute the pipeline.

### Step 2 — Investigate in the Monitoring Hub

1. Open the **Monitoring Hub** → **Activities** page.
2. Find the failed pipeline run (red ✖ status).
3. Select **View details** (ℹ️):
   - **Status:** Failed
   - **Error Message:** Something like `Invalid object name 'SalesLT.NonExistentTable'`
   - **Failed Activity:** `Copy_IncrementalSales`
4. Note the following diagnostic information:
   - **Error Code** — Useful for searching documentation or support tickets
   - **Failure Type** — User error vs. system error
   - **Activity Duration** — How long before the failure occurred

### Step 3 — Fix and Re-Run

1. Return to the pipeline editor.
2. Fix the source query back to the correct table name.
3. Re-run the pipeline.
4. Verify in the Monitoring Hub that the new run succeeds.

> **✅ Checkpoint:** You successfully traced a pipeline failure from symptom (red status) to root cause (wrong table name) using only the Monitoring Hub.

---

## Exercise 3 — Schedule Failure Notifications

**Time:** ~5 minutes  
**Goal:** Configure email notifications for scheduled job failures.

### What are Schedule Failure Notifications?

Instead of manually checking the Monitoring Hub, you can configure **automatic email notifications** that fire whenever a scheduled job fails. This is critical for production pipelines where failures need immediate attention.

### Steps

1. In the Monitoring Hub, select **Schedule failures** under **Configure notifications** in the side panel.
2. On the **Schedule failures** page:
   - You see all items with failure notifications already configured.
3. Select **+ Configure notifications**.
4. Select an item from the list:
   - Choose `PL_E2E_DataIntegration` (the pipeline from Module 2).
5. Enter the **recipients**:
   - Add one or more email addresses (users or groups in your Microsoft Entra tenant).
   - B2B guest users are also supported.
6. Select **Save**.

### Verify the Configuration

7. The configured item now appears in the **Schedule failures** table.
8. To edit or remove:
   - Select the item → The **Notification details** pane opens.
   - Select **Edit recipients** to change recipients.
   - Select **Remove notifications** to stop notifications.

> **Note:** Notifications apply only to **scheduled runs** — manually triggered runs do not trigger notifications.

> **✅ Checkpoint:** Failure notifications are configured for your pipeline. When the scheduled run fails, recipients receive an email with error details.

---

## Exercise 4 — Monitoring Spark Applications

**Time:** ~5 minutes  
**Goal:** Browse Spark applications in the Monitoring Hub to understand notebook and Mapping Data Flow execution.

### Steps

1. In the Monitoring Hub, filter by **Item type** = `Notebook` or `Spark Job Definition`.
2. Select a completed notebook run → **View details**.
3. In the details pane, look for:
   - **Spark application ID** — Links to the Spark UI
   - **Duration breakdown** — Queue time vs. execution time
   - **Stage progress** — Number of tasks completed vs. total

4. Select the **Spark application ID** (or navigate to **Monitor** → **Apache Spark applications**) to open the Spark monitoring view:
   - **Jobs tab**: See all Spark jobs triggered by the notebook
   - **Stages tab**: View individual stages with task-level metrics
   - **Executors tab**: Check executor memory and CPU usage
   - **Diagnostics tab**: View any errors, warnings, or skewed tasks

5. **Identify performance bottlenecks:**
   - Look for stages with very different task durations (data skew).
   - Check if executors are under-utilized (too many executors for a small dataset).
   - Review shuffle read/write metrics for expensive join operations.

> **✅ Checkpoint:** You can navigate from the Monitoring Hub to the Spark UI, understand job/stage/task hierarchy, and identify common performance issues.

---

## Exercise 5 — Capacity Metrics and Telemetry

**Time:** ~5 minutes  
**Goal:** Understand how data integration activities consume Fabric capacity and where to find consumption metrics.

### Understanding Capacity Consumption

Every Fabric activity consumes **Capacity Units (CUs)**. Understanding consumption helps you:
- Right-size your Fabric capacity (F2, F4, F8, ... F8192)
- Identify expensive operations that need optimization
- Plan budgets and allocate costs across teams

### Key Concepts

| Concept | Description |
|---|---|
| **CU (Capacity Unit)** | The unit of compute in Fabric — all workloads share the same pool |
| **Smoothing** | Fabric smooths burst consumption over 24 hours — short spikes don't cause throttling |
| **Throttling** | If sustained consumption exceeds capacity, jobs queue or slow down |
| **Interactive vs. Background** | Interactive (queries, previews) and background (pipelines, refreshes) consume CUs differently |

### Where to Find Capacity Metrics

1. **Microsoft Fabric Capacity Metrics App:**
   - Install from AppSource: Search for **"Microsoft Fabric Capacity Metrics"**
   - This Power BI app connects to your capacity and shows:
     - CU consumption over time
     - Consumption by workload (Data Factory, Spark, SQL, Power BI)
     - Top consuming items
     - Throttling events

2. **Admin Portal:**
   - Navigate to **Settings** (gear icon) → **Admin portal** → **Capacity settings**.
   - Select your capacity to see:
     - Current utilization percentage
     - Background vs. interactive consumption
     - Recent throttling events

### Capacity Consumption by Data Integration Activity

| Activity | Capacity Impact | Optimization Tips |
|---|---|---|
| **Copy Job** | Low → Medium (depends on data volume) | Use incremental/CDC to reduce volume |
| **Copy Activity** | Low → Medium | Tune parallelism; enable staging for cross-region |
| **Mirroring** | Free (replication compute is not charged) | Free storage up to 1 TB per CU |
| **Eventstream** | Medium (continuous processing) | Filter events early to reduce throughput |
| **Dataflow Gen2** | Low → Medium (mashup engine) | Reduce query folding where possible |
| **Mapping Data Flow** | Medium → High (Spark cluster) | Optimize partition count; limit data preview during development |
| **Notebook** | Medium → High (Spark cluster) | Right-size Spark pool; cache DataFrames; use broadcast joins |
| **Pipeline** | Low (orchestration overhead only) | Minimize idle wait time between activities |

> **✅ Checkpoint:** You understand how Fabric capacity consumption works and know where to find metrics for your data integration workloads.

---

## Monitoring & Telemetry Cheat Sheet

### Quick Diagnostics Workflow

```
1. Pipeline failed?
   └─▶ Monitoring Hub → Filter: Status = Failed
       └─▶ View Details → Read error message
           └─▶ Fix the root cause → Re-run

2. Performance degraded?
   └─▶ Monitoring Hub → Historical runs → Compare durations
       └─▶ Spark UI → Check for data skew / under-utilization
           └─▶ Optimize (repartition, broadcast, filter early)

3. Scheduled job not running?
   └─▶ Check the trigger schedule on the item
       └─▶ Verify capacity is not paused/throttled
           └─▶ Check Admin Portal for capacity utilization

4. Need proactive alerts?
   └─▶ Configure Schedule Failure Notifications
       └─▶ Add Teams/email webhook in pipeline On-Failure path
```

### Useful Links

| Resource | URL |
|---|---|
| Monitoring Hub Overview | [learn.microsoft.com/fabric/admin/monitoring-hub](https://learn.microsoft.com/en-us/fabric/admin/monitoring-hub) |
| Spark Application Monitoring | [learn.microsoft.com/fabric/data-engineering/browse-spark-applications-monitoring-hub](https://learn.microsoft.com/en-us/fabric/data-engineering/browse-spark-applications-monitoring-hub) |
| Dataflow Gen2 Monitoring | [learn.microsoft.com/fabric/data-factory/dataflows-gen2-monitor](https://learn.microsoft.com/en-us/fabric/data-factory/dataflows-gen2-monitor) |
| Capacity Metrics App | [learn.microsoft.com/fabric/enterprise/metrics-app](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app) |
| Job Scheduler | [learn.microsoft.com/fabric/fundamentals/job-scheduler](https://learn.microsoft.com/en-us/fabric/fundamentals/job-scheduler) |
| Decision Guide | [learn.microsoft.com/fabric/fundamentals/decision-guide-pipeline-dataflow-spark](https://learn.microsoft.com/en-us/fabric/fundamentals/decision-guide-pipeline-dataflow-spark) |

---

## Module 4 Summary

In this module you learned to:

1. **Navigate the Monitoring Hub** — View active and historical activities across all Fabric workloads.
2. **Investigate failures** — Trace from a failed status to the root cause error message.
3. **Configure alerts** — Set up schedule failure notifications for proactive monitoring.
4. **Monitor Spark** — Drill into Spark UI to understand job/stage/task execution and identify bottlenecks.
5. **Understand capacity** — Know how each data integration activity consumes CUs and where to find metrics.

---

## Workshop Wrap-Up

Congratulations! You have completed the **Fabric Data Integration in a Day** workshop. Here's what you accomplished:

| Module | What You Did |
|---|---|
| **Module 1 — Data Movement** | Ingested data using Copy Job (full + incremental), Mirroring (near real-time), Copy Activity (pipeline), and Eventstream (streaming) |
| **Module 2 — Orchestration** | Built a visual Pipeline (low-code) and an Apache Airflow DAG (code-first) to orchestrate end-to-end workflows |
| **Module 3 — Transformation** | Transformed data using Mapping Data Flow (visual), Dataflow Gen2 (Power Query), and Notebook (PySpark) |
| **Module 4 — Monitoring** | Monitored execution, investigated failures, configured alerts, and reviewed capacity consumption |

### Architecture You Built

```
External Sources (SQL DB · ADLS · Event Hubs)
        │
        ▼
┌─── Data Movement ──────────────────────────────────┐
│ Copy Job │ Mirroring │ Copy Activity │ Eventstream  │
└────────────────────┬───────────────────────────────┘
                     ▼
            ┌─── Bronze Layer ───┐
            │   (Raw Data)       │
            └────────┬───────────┘
                     ▼
┌─── Transformation ─────────────────────────────────┐
│ Mapping Data Flow │ Dataflow Gen2 │ Notebook        │
└────────────────────┬───────────────────────────────┘
                     ▼
            ┌─── Silver / Gold ──┐
            │  (Analytics-Ready) │
            └────────┬───────────┘
                     ▼
┌─── Orchestration ──────────────────────────────────┐
│       Pipeline (Low-code) │ Airflow (Code-first)   │
└────────────────────┬───────────────────────────────┘
                     ▼
┌─── Monitoring & Telemetry ─────────────────────────┐
│  Monitoring Hub │ Spark UI │ Capacity Metrics       │
└────────────────────────────────────────────────────┘
```

### Next Steps

- **Power BI**: Build reports on top of the Gold layer using Direct Lake mode.
- **Data Agent**: Create a Fabric Data Agent to answer natural-language questions over your warehouse.
- **CI/CD**: Set up Git integration and deployment pipelines for your Fabric artifacts.
- **Governance**: Configure data lineage, sensitivity labels, and access controls.

---

**Previous:** [← Module 3 — Transformation](Module-03-Transformation.md)  
**Back to:** [README — Workshop Overview](../../README.md)
