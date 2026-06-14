---
title: "Why I’m finally ditching Hive Metastore for BigLake Iceberg"
published: true
description: "A field guide to migrating production workloads to BigLake Iceberg on GCP, avoiding common pitfalls and performance traps."
tags: gcp, iceberg, bigquery, dataengineering
cover_image: https://images.unsplash.com/photo-1744868562210-fffb7fa882d9?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5NzU0MjJ8MHwxfHNlYXJjaHw1fHxzZXJ2ZXIlMjByb29tJTIwY2FibGVzfGVufDB8MHx8fDE3ODE0MTExMzB8MA&ixlib=rb-4.1.0&q=80&w=1080
canonical_url:
---

In the six years I’ve spent wrestling with data stacks in fintech and healthcare, I’ve seen enough Hive Metastore corruptions to last a lifetime. If you’ve ever had to manually fix a partition mismatch because someone ran an `MSCK REPAIR` at the wrong time, you know the pain. When Google announced BigLake support for Apache Iceberg, I was skeptical. I’ve seen “open” standards become vendor-locked nightmares before.

But after six months of moving production telemetry and PII-heavy healthcare logs onto the BigLake/Iceberg stack, I’m sold—with caveats. This isn’t a marketing whitepaper. This is a breakdown of what happens when you actually try to query Petabyte-scale Iceberg tables from BigQuery without blowing your budget or your sanity.

## 1. Stop treating BigLake like a legacy External Table
The biggest mistake I see engineers make is treating BigLake Iceberg tables like standard Parquet-on-GCS external tables. They aren't. BigLake is a storage engine wrapper that enforces fine-grained access control (FGAC) at the table, row, and column level via BigQuery.

When you define your table, don't just point it at a GCS bucket and hope. You need to use the `bq` CLI or Terraform to explicitly define the metadata storage. If you rely on the hive-style partitioning in your folder structure, you’re missing the point of Iceberg’s hidden partitioning. Use the Iceberg metadata to handle the partitioning, not your folder names.

```hcl
resource "google_bigquery_table" "iceberg_table" {
  table_id   = "patient_records"
  dataset_id = "healthcare_lake"
  type       = "EXTERNAL"
  
  external_data_configuration {
    connection_id = "projects/my-proj/locations/us/connections/biglake-conn"
    source_format = "ICEBERG"
    source_uris   = ["gs://my-bucket/iceberg-metadata/metadata.json"]
  }
}
```

![Photo by Wandering khan on Unsplash](https://images.unsplash.com/photo-1735242004603-17d868f7fbce?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5NzU0MjJ8MHwxfHNlYXJjaHwyOHx8bW91bnRhaW4lMjBsYWtlJTIwcmVmbGVjdGlvbnxlbnwwfDB8fHwxNzgxNDExMDY2fDA&ixlib=rb-4.1.0&q=80&w=1080)
*Photo by [Wandering khan](https://unsplash.com/@iamwanderingkhan?utm_source=articles_pipeline&utm_medium=referral) on [Unsplash](https://unsplash.com/?utm_source=articles_pipeline&utm_medium=referral)*


## 2. Compaction is not optional (it’s a tax)
If you are streaming data into Iceberg, you are creating small files. If you don't compact them, your BigQuery performance will crater, and your bill will balloon because BQ will be scanning thousands of tiny objects.

I set up a recurring Dataproc Serverless job to run `rewrite_data_files` using the Spark Iceberg library. Don't wait for your users to complain about latency. If your table sees high ingestion, run compaction every 4 hours.

```python
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("IcebergCompaction").getOrCreate()
spark.sql("CALL catalog.system.rewrite_data_files(table => 'healthcare.patient_records')")
```

## 3. Row-level security (RLS) is the killer feature
In healthcare, we spend half our lives worrying about HIPAA compliance. With legacy Parquet, I had to create views upon views to mask PII columns. With BigLake, I define the policy once on the Iceberg table in BigQuery, and it applies regardless of whether the user is querying via `bq query`, Looker, or a custom Python script.

Use `ALTER TABLE` to add row access policies. It’s cleaner than managing IAM roles for every single data analyst on the team.

```sql
CREATE ROW ACCESS POLICY patient_privacy_filter
ON `healthcare_lake.patient_records`
GRANT TO ("user:analyst@company.com")
FILTER USING (region = 'US');
```

## 4. Watch your Manifest File versions
Iceberg relies on manifest files to track snapshots. In my first month, I had a job that failed midway through a commit. The result? A dangling snapshot that wasn't being cleaned up. 

If you don't run `expire_snapshots`, your metadata folder will grow until it hits GCS object limits, or worse, your `select *` queries start timing out while BigQuery tries to parse a million tiny manifest files. Run this procedure weekly:

```sql
CALL my_dataset.system.expire_snapshots(
  table => 'patient_records',
  older_than => TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
);
```

## 5. The "Schema Evolution" trap
Iceberg supports schema evolution (adding columns, renaming). This is great until you realize downstream consumers (like older Spark jobs or legacy BI tools) don't handle `ALTER TABLE RENAME` well. 

The rule here is simple: never rename a column if you can avoid it. Add new columns, deprecate the old ones. If you rename, you risk breaking every `SELECT *` query in your organization. If you *must* rename, use the Iceberg field IDs to maintain continuity, but be prepared for downstream friction.

## 6. The "Connection" bottleneck
BigLake requires a Cloud Resource Connection. If you have 500+ tables, don't create 500 connections. You’ll hit the project quota for connection resources instantly. 

Group your tables by security boundary. Put all your clinical data in one connection, and your administrative data in another. This keeps your IAM manageable and ensures you aren't fighting Google’s backend quotas on a Friday night.

![Photo by Jake Walker on Unsplash](https://images.unsplash.com/photo-1608742213509-815b97c30b36?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5NzU0MjJ8MHwxfHNlYXJjaHwxfHxjb2RlJTIwb24lMjB0ZXJtaW5hbHxlbnwwfDB8fHwxNzgxNDExMTMxfDA&ixlib=rb-4.1.0&q=80&w=1080)
*Photo by [Jake Walker](https://unsplash.com/@jakewalker?utm_source=articles_pipeline&utm_medium=referral) on [Unsplash](https://unsplash.com/?utm_source=articles_pipeline&utm_medium=referral)*


## 7. Performance isn't magic (The "Small File" rule)
There is a persistent myth that Iceberg makes Parquet fast. It doesn't. Iceberg makes metadata management fast. If your underlying Parquet files are 10MB each, you are losing. Aim for file sizes between 128MB and 512MB. 

If your ingestion pattern creates small files, you need to use a middleware like Flink or a Spark-based streaming job that buffers data into appropriately sized chunks before committing to the Iceberg table. If I see one more person trying to "optimize" a 2GB table made of 200,000 files by just adding more compute, I’m going to lose my mind.

## Conclusion
BigLake on Iceberg is the first time I’ve felt like I’m building a truly open data platform on Google Cloud without sacrificing the integration features that make BigQuery tolerable. You get the benefits of an open format (GCS + Parquet) with the governance of a warehouse.

It requires discipline. You have to be the janitor of your own metadata, you have to be religious about compaction, and you have to respect the constraints of the connection manager. If you treat it like a "set it and forget it" system, you’ll end up with a high-latency, high-cost mess. But if you put in the engineering rigor, it’s the most resilient architecture I’ve deployed to date.

Are you still relying on manual partition refreshes for your data lake, or have you finally made the switch to a managed metadata layer?

***

**Tags:** #gcp #iceberg #bigquery #dataengineering

*Cover photo by [Albert Stoynov](https://unsplash.com/@albertstoynov?utm_source=articles_pipeline&utm_medium=referral) on [Unsplash](https://unsplash.com/?utm_source=articles_pipeline&utm_medium=referral).*
