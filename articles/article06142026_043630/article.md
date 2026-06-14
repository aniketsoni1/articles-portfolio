---
title: "How I Finally Killed the Full-Refresh Silver Layer"
published: true
description: "Stop rewriting your entire silver layer every time a dimension changes. Here is how to use Delta Change Data Feed for true incremental ETL."
tags: delta, databricks, spark, dataengineering
cover_image: https://images.unsplash.com/photo-1767972161406-93e1f11a5c13?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5NzU0MjJ8MHwxfHNlYXJjaHwxNXx8YnJva2VuJTIwY2xvY2t8ZW58MHwwfHx8MTc4MTQxMTc4OXww&ixlib=rb-4.1.0&q=80&w=1080
canonical_url:
---

Full-refresh pipelines are a confession of incompetence. If you are still running massive batch jobs that overwrite your entire Silver layer because you’re afraid of handling deletes or updates, you are burning compute credits to hide your inability to manage state.

> **Why I chose this topic:** I spent three months cleaning up a nightmare pipeline where a 4TB table was being overwritten daily because the original team didn't trust their merge logic. Incremental processing isn't just about speed; it's about building systems that don't fall apart when the data volume hits a breaking point.

## Why the common approach falls short

Most engineers default to `overwrite` mode because it’s "safe." If you screw up an incremental merge, you have duplicate records or orphaned deletes. So, you dump the whole dataset into a `df.write.format("delta").mode("overwrite")` block and walk away. 

But this fails the moment your business requires sub-hour latency or your source system grows beyond the point where a full scan finishes in the allotted window. In financial services, I’ve seen this strategy cause "data drift" where downstream reports reflect the morning’s state while the warehouse is still grinding through a 6-hour full overwrite. You aren't building a data lake; you're building a fragile, expensive, and stale batch report.

![Photo by imgix on Unsplash](https://images.unsplash.com/photo-1506399309177-3b43e99fead2?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5NzU0MjJ8MHwxfHNlYXJjaHw4fHxzZXJ2ZXIlMjByYWNrfGVufDB8MHx8fDE3ODE0MTE3ODl8MA&ixlib=rb-4.1.0&q=80&w=1080)
*Photo by [imgix](https://unsplash.com/@imgix?utm_source=articles_pipeline&utm_medium=referral) on [Unsplash](https://unsplash.com/?utm_source=articles_pipeline&utm_medium=referral)*


## Enabling CDF at the source

The magic is in the `delta.enableChangeDataFeed` table property. Do not turn this on after the fact unless you have a weekend to kill and enough disk space to handle the metadata explosion. Set it at table creation:

```sql
CREATE TABLE raw_transactions (
  id LONG,
  amount DOUBLE,
  updated_at TIMESTAMP
)
USING DELTA
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');
```

Once this is enabled, Delta stores the row-level changes in a hidden directory. You are no longer just looking at the current state of the table; you are looking at the history of how the table got there. 

When you read from this, you aren't scanning the entire table. You are scanning the transaction log. If you are using Spark, you use the `readChangeFeed` option:

```python
df = spark.read \
  .format("delta") \
  .option("readChangeFeed", "true") \
  .option("startingVersion", 0) \
  .table("raw_transactions")
```

This returns a stream containing `_change_type` columns: `insert`, `update_preimage`, `update_postimage`, and `delete`. Your Silver layer transformation code shifts from "calculate the state" to "apply the delta."

## Building the incremental Silver layer

The goal is to maintain a stateful Silver table that receives only the modifications. Your Silver transformation job should be a streaming query that performs a `merge` into the target.

```python
def upsert_to_silver(batch_df, batch_id):
    batch_df.createOrReplaceTempView("updates")
    
    # We only care about the post-image of updates and new inserts
    filtered_updates = spark.sql("""
        SELECT * FROM updates 
        WHERE _change_type IN ('insert', 'update_postimage')
    """)
    
    # Upsert logic
    target_table.alias("target").merge(
        filtered_updates.alias("source"),
        "target.id = source.id"
    ).whenMatchedUpdateAll() \
     .whenNotMatchedInsertAll() \
     .execute()
```

If you have a `delete` from the source, you need to handle that explicitly. The `_change_type` will be `delete`. You map that to a `target.delete()` command in your merge operation. This is where most people get stuck—they forget that a Silver layer is a reflection of current state, and deletes are part of that state. If your source system hard-deletes, your Silver layer must mirror that, or you're effectively lying to your stakeholders.

## Failure modes you will encounter

CDF is not a silver bullet; it’s a tool that requires discipline. The biggest failure mode is "log retention." By default, Delta Lake keeps history for 30 days. If your Silver pipeline fails for 31 days, you cannot catch up. You will have to do a full snapshot refresh anyway.

Monitor the `delta.logRetentionDuration`. If you are in a high-compliance environment, set this to 365 days or even "forever" if you have the storage. Yes, it increases the metadata overhead. No, you don't have a choice if you want to ensure recoverability.

Another common failure is schema evolution. If you add a column to your Raw layer, and your Silver merge logic doesn't expect it, your stream will crash. I recommend explicitly selecting columns in your Silver transform rather than using `*`. It makes the job verbose, but it saves your pipeline from breaking when a source system adds a metadata column you don't even need.

![Photo by Vishnu Mohanan on Unsplash](https://images.unsplash.com/photo-1640955785023-1854685dae05?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5NzU0MjJ8MHwxfHNlYXJjaHw2fHxjaXJjdWl0JTIwYm9hcmR8ZW58MHwwfHx8MTc4MTQxMTc5MHww&ixlib=rb-4.1.0&q=80&w=1080)
*Photo by [Vishnu Mohanan](https://unsplash.com/@vishnumaiea?utm_source=articles_pipeline&utm_medium=referral) on [Unsplash](https://unsplash.com/?utm_source=articles_pipeline&utm_medium=referral)*


## The objections (and my answers)

"But full-refresh is easier to debug." 
Sure, it’s easier to debug if you define "debugging" as "deleting the table and restarting." That isn't engineering; that's hitting the 'reset' button on a console. If you use CDF, you can replay the feed from a specific `startingVersion` to verify exactly how a record reached its current state. It is infinitely more transparent than a daily overwrite.

"CDF adds too much storage overhead."
It adds roughly 10–20% to your storage costs for the metadata. Meanwhile, your compute costs for daily full-refreshes are likely 300–400% higher than they need to be. If you are worried about the cost of storing a few extra JSON files in the `_change_data` directory, you are optimizing the wrong end of the bill. Compute is the enemy; storage is cheap.

"What if the source isn't Delta?"
If your source is Kafka, use the Kafka log as your CDF. If your source is a legacy RDBMS, use Debezium to generate the Change Data Capture (CDC) events and ingest them into Delta. The pattern remains the same: treat the incoming stream as the source of truth, and merge it into the target. Never, ever dump a full table scan into a write operation.

## Conclusion

The era of "nightly batch" is over. Modern financial and healthcare data demands require us to treat data as a living stream, not a static file that gets replaced every 24 hours. Change Data Feed is the most robust way to enforce this. It forces you to handle updates and deletes as first-class citizens, it reduces your compute spend, and it provides a perfect audit trail of your data's evolution. Stop the full-refresh cycle. Your cloud bill—and your on-call sanity—will thank you.

---

**Tags:** #delta #spark #dataengineering #databricks

*Cover photo by [Sasun Bughdaryan](https://unsplash.com/@sasun1990?utm_source=articles_pipeline&utm_medium=referral) on [Unsplash](https://unsplash.com/?utm_source=articles_pipeline&utm_medium=referral).*
