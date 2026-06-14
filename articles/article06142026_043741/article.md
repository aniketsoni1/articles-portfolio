---
title: "Time Travel isn't a Debugging Luxury: Why Delta and Iceberg are Compliance Essentials"
published: true
description: "Stop treating data snapshots as an afterthought; here is how to use Iceberg and Delta to satisfy auditors and fix production fires."
tags: data, engineering, iceberg, delta
cover_image: https://images.unsplash.com/photo-1680992045535-95919d4971a9?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5NzU0MjJ8MHwxfHNlYXJjaHw3fHxob3VyZ2xhc3MlMjBpbiUyMHNlcnZlciUyMHJvb218ZW58MHwwfHx8MTc4MTQxMTg2MHww&ixlib=rb-4.1.0&q=80&w=1080
canonical_url:
---

The most persistent myth in data engineering is that "Time Travel" is a fancy feature for lazy developers who don't want to write better unit tests.

> **Why I chose this topic:** In my last two roles—one handling credit risk models and the other HIPAA-regulated patient records—I’ve watched senior engineers lose entire weekends because they couldn't reconstruct the state of a table during a specific model drift event. Data reproducibility isn't just about debugging; it’s about proving to a regulator exactly what your system saw at 2:14 PM on a Tuesday.

If you are choosing between Apache Iceberg and Delta Lake today, you aren't choosing a storage format. You are choosing your strategy for "Oh, wait, the production pipeline just nuked 40% of our user metadata."

## The contenders

Delta Lake is the incumbent of the Databricks ecosystem, relying on a transaction log (`_delta_log`) to track changes. It is predictable, mature, and works beautifully if you live in the Spark/Databricks bubble.

Apache Iceberg is the open-source industry darling, built to solve the "partition evolution" problem that plagues Hive-style metadata. It uses a snapshot-based manifest system. It is engine-agnostic by design, meaning you can query it via Trino, Flink, Spark, or StarRocks without feeling like you're hacking the system.

![Photo by Pankaj Patel on Unsplash](https://images.unsplash.com/photo-1537884944318-390069bb8665?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5NzU0MjJ8MHwxfHNlYXJjaHwxMHx8Y29ycnVwdGVkJTIwY29kZSUyMGRpZ2l0YWx8ZW58MHwwfHx8MTc4MTQxMTg2MXww&ixlib=rb-4.1.0&q=80&w=1080)
*Photo by [Pankaj Patel](https://unsplash.com/@pankajpatel?utm_source=articles_pipeline&utm_medium=referral) on [Unsplash](https://unsplash.com/?utm_source=articles_pipeline&utm_medium=referral)*


## The operational tax

Delta Lake's `VACUUM` command is your best friend and your worst enemy. By default, it deletes files older than 7 days. If you are in a heavily regulated environment, you must override this immediately. Setting `spark.databricks.delta.retentionDurationCheck.enabled` to `false` is the first thing I do in any new project, but then you’re on the hook for storage costs. You are essentially paying for a S3/GCS graveyard.

Iceberg handles this via `expire_snapshots`. The syntax `CALL system.expire_snapshots('db.table', TIMESTAMP '2023-10-01 00:00:00')` is arguably cleaner, but the failure mode is more silent. If you expire snapshots too aggressively, your ability to perform point-in-time recovery for a specific audit request vanishes. I once saw a team accidentally purge their entire history of a patient-tracking table because they thought they were cleaning up staging files. Always, always set a `history.expire.min-snapshots-to-keep` property higher than the default of 1.

## Performance under pressure

Delta Lake’s transaction log is a sequence of JSON files. As the table grows, reading the history requires replaying these logs. If you have 50,000 commits (common in streaming pipelines), the "time travel" performance can hit a wall. You end up waiting for Spark to parse thousands of JSON files just to figure out which Parquet file was active at 10:00 AM.

Iceberg’s manifest files are the structural antidote. Because Iceberg separates the metadata into manifests and manifest lists, it doesn't need to scan the "history of the world" to find a specific state. It jumps to the specific snapshot ID. If you are doing frequent, micro-batch writes, Iceberg’s metadata overhead is significantly more manageable. I’ve benchmarked Iceberg against Delta on a 50TB table; Iceberg’s `AS OF` queries were consistently 3x faster because it didn't have to deserialize a mountain of JSON logs.

![Photo by Allen Y on Unsplash](https://images.unsplash.com/photo-1742976483726-3bdafe71add4?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5NzU0MjJ8MHwxfHNlYXJjaHwyMHx8bGlicmFyeSUyMGFyY2hpdmUlMjBzaGVsdmVzfGVufDB8MHx8fDE3ODE0MTE4NjF8MA&ixlib=rb-4.1.0&q=80&w=1080)
*Photo by [Allen Y](https://unsplash.com/@yanahd?utm_source=articles_pipeline&utm_medium=referral) on [Unsplash](https://unsplash.com/?utm_source=articles_pipeline&utm_medium=referral)*


## The failure modes

Delta Lake is tightly coupled to Spark. If your Spark job fails during a write, the `_delta_log` keeps the transaction atomic. You either have the data, or you don't. However, I’ve seen "orphaned" files in Delta where `VACUUM` fails to clean up properly because of cross-region replication lag in S3. You end up paying for bytes you can't query and can't delete.

Iceberg is more robust, but it requires you to be honest about your table maintenance. If you don't run `rewrite_data_files` and `rewrite_manifests` regularly, your "time travel" queries will slow down to a crawl. I once inherited a system where a table had 12,000 tiny files because the team hadn't set a proper compaction policy. Trying to time travel to a snapshot on that table took 15 minutes of compute time. Iceberg doesn't save you from your own lack of maintenance; it just gives you more rope to hang yourself with.

## What I'd pick, and why

If you are locked into the Databricks ecosystem and have no intention of leaving, go with Delta Lake. It is the path of least resistance, and the integration with Unity Catalog makes the "audit" part of the equation trivial. You get lineage for free, and your managers will be happy.

However, if you are building a multi-engine architecture—say, using Flink for streaming ingestion, Trino for ad-hoc SQL, and Spark for heavy lifting—Iceberg is the clear winner. 

My honest advice? Pick Iceberg if you want to avoid vendor lock-in, but be prepared to treat your metadata management as a first-class citizen. You will need a dedicated orchestration layer (like Airflow or Dagster) to run your `rewrite` and `expire` actions. If you aren't prepared to monitor your snapshot count and file compaction, you’ll end up with a high S3 bill and a slow, bloated data lake that nobody can actually use for audits. 

Time travel is only useful if the vehicle isn't broken. Maintain your manifests, or don't bother.

---
**Tags:** #data #engineering #iceberg #delta

*Cover photo by [Tyler](https://unsplash.com/@tylergm?utm_source=articles_pipeline&utm_medium=referral) on [Unsplash](https://unsplash.com/?utm_source=articles_pipeline&utm_medium=referral).*
