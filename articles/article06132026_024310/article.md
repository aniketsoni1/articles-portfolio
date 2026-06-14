---
title: "Cutting Snowflake compute costs 40 percent: warehouse sizing, auto-suspend, and query pruning"
published: true
description: "Stop burning your cloud budget. A senior engineer’s guide to slashing Snowflake costs using warehouse tuning and query optimization."
tags: snowflake, cloud, data, engineering
cover_image: https://images.unsplash.com/photo-1558494949-ef010cbdcc31?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5NzU0MjJ8MHwxfHNlYXJjaHwxfHxzZXJ2ZXIlMjByYWNrfGVufDB8MHx8fDE3ODEyOTM4Njh8MA&ixlib=rb-4.1.0&q=80&w=1080
canonical_url:
---

> **Why I chose this topic:** I’ve walked into three different data teams in the last two years where the Snowflake bill was treated like a "cost of doing business" rather than a system to be optimized. Watching a company burn $15k a month because of a default `AUTO_SUSPEND` setting is painful. This isn't just about saving money; it’s about engineering discipline. If you can’t manage your cloud resources, you aren't building a platform—you're just renting an expensive sandbox.

I remember my first week at a healthcare fintech firm back in 2021. The CFO sent an email to the engineering Slack channel with a screenshot of our AWS/Snowflake bill. It was red, aggressive, and frankly, embarrassing. We had a warehouse running 24/7, processing a dashboard that three people looked at once a week. 

The worst part? We were running an `X-Large` warehouse for a workload that could have been handled by an `X-Small` if we’d just bothered to look at the query profile. We were literally lighting money on fire to keep the lights on for ghosts. 

If your Snowflake bill has grown linearly with your data volume, you’re doing it wrong. You aren't paying for data storage; you’re paying for compute waste. Let’s fix it.

## The real problem: "Default" is a trap

Snowflake’s defaults are designed for developer experience, not your bottom line. They want you to have a seamless experience where queries just work, immediately, without you having to think about infrastructure. 

But "just works" is expensive.

When you create a warehouse without thinking about the workload, you’re usually defaulting to settings that keep compute active long after the last query finishes. You’re also likely using a warehouse size that assumes you have massive concurrency, even if you’re just running a few heavy ELT jobs.

![Photo by Deng Xiang on Unsplash](https://images.unsplash.com/photo-1666875753105-c63a6f3bdc86?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5NzU0MjJ8MHwxfHNlYXJjaHwxfHxkYXRhJTIwcGlwZWxpbmV8ZW58MHwwfHx8MTc4MTI5Mzg2OHww&ixlib=rb-4.1.0&q=80&w=1080)
*Photo by [Deng Xiang](https://unsplash.com/@dengxiangs?utm_source=articles_pipeline&utm_medium=referral) on [Unsplash](https://unsplash.com/?utm_source=articles_pipeline&utm_medium=referral)*


## Step 1: Aggressive Auto-Suspend

If your warehouse is set to `AUTO_SUSPEND = 600` (10 minutes), stop reading this and change it now. Unless you are running a high-concurrency BI tool where millisecond latency is the difference between life and death, you don't need a 10-minute idle window.

For most ELT workloads, a 60-second suspend is the sweet spot. If it’s a batch job that runs once an hour, set it to 60 seconds. If it’s a heavy transformation, set it to 60 seconds.

```sql
-- Check your current settings
SHOW WAREHOUSES LIKE 'PROD_WH';

-- Lower that suspend time immediately
ALTER WAREHOUSE PROD_WH SET AUTO_SUSPEND = 60;
```

Why 60? Because Snowflake charges per-second after the first 60 seconds of a credit cycle. If you suspend at 60 seconds, you stop the meter. If you wait 10 minutes, you’re paying for 9 minutes of idle time. In a month, that’s thousands of dollars of "nothing" being billed to your credit card.

## Step 2: Right-sizing (The "Goldilocks" approach)

Engineers love "X-Large" warehouses because they make queries fast. Fast is good, right? Not if you’re paying for 16 nodes when 2 nodes would have finished the job in the same amount of time.

Snowflake scales vertically. If you have a query that takes 10 minutes on an `X-Small` and you bump it to an `X-Large`, it might finish in 2 minutes. You’ve used the same number of credits (roughly), but you’ve wasted the idle time.

My rule: Start at `X-Small`. If the query runs and hits the `Query Profile` "Spilling to Local Storage" or "Spilling to Remote Storage" warnings, only then do you scale up.

```sql
-- Check for spilling in your history
SELECT 
    query_id, 
    bytes_spilled_to_local_storage, 
    bytes_spilled_to_remote_storage
FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY())
WHERE bytes_spilled_to_remote_storage > 0
ORDER BY start_time DESC;
```

If you see high spill numbers, your warehouse is too small for the data volume. If you see zero spill and low utilization, your warehouse is too big. Scale down.

## Step 3: Pruning and Clustering

Snowflake is a columnar store. If you are scanning the entire table to find one user’s data, you are failing at the most basic level of Snowflake performance. 

Micro-partitioning is the magic, but you have to feed it correctly. Use `CLUSTERING KEYS` on columns you filter by most often (e.g., `created_at` or `tenant_id`). 

```sql
-- Define a clustering key for a large table
ALTER TABLE events_raw 
CLUSTER BY (DATE_TRUNC('DAY', event_timestamp), tenant_id);
```

When you query, ensure your `WHERE` clauses match your clustering keys. If you’ve clustered by `event_timestamp`, don't filter by `user_id` alone. If you do, Snowflake has to perform a full table scan. A full table scan on a multi-terabyte table is a budget killer.

![Photo by Chris Ried on Unsplash](https://images.unsplash.com/photo-1515879218367-8466d910aaa4?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5NzU0MjJ8MHwxfHNlYXJjaHwxfHxjb2RlJTIwcmV2aWV3fGVufDB8MHx8fDE3ODEyOTM4Njl8MA&ixlib=rb-4.1.0&q=80&w=1080)
*Photo by [Chris Ried](https://unsplash.com/@cdr6934?utm_source=articles_pipeline&utm_medium=referral) on [Unsplash](https://unsplash.com/?utm_source=articles_pipeline&utm_medium=referral)*


## Lessons learned from production

I once worked with a team that had "Query Timeout" alerts set to 4 hours. That’s insane. If a query takes more than 30 minutes, something is fundamentally broken—either a Cartesian product or a massive data skew issue.

Set a `STATEMENT_TIMEOUT_IN_SECONDS` at the warehouse level.

```sql
ALTER WAREHOUSE PROD_WH SET STATEMENT_TIMEOUT_IN_SECONDS = 1800;
```

If a query hits this, it dies. It doesn't keep burning credits for three more hours. It forces the developer to look at the `Query Profile` and fix the `JOIN` logic. We saw a 15% reduction in compute just by killing runaway queries that were stuck in infinite loops or massive cross-joins.

## Production considerations

1. **Resource Monitors:** Set them. Seriously. Create a monthly limit that alerts you at 50%, 75%, and 90%. Set the 100% action to `SUSPEND_IMMEDIATE`. Don't let a rogue process bankrupt the department.
2. **Dedicated Warehouses:** Do not share a warehouse between your BI dashboards and your dbt/Airflow jobs. BI users are unpredictable; transformation jobs are steady. When they fight for resources, you end up over-sizing the warehouse just to handle the "spikes," which is a waste of money.
3. **Query Profile:** Before you call "done" on a pull request, open the Query Profile in the Snowflake UI. If you see a "Join" operator that is scanning 100% of the table, you have work to do.

## Conclusion

Snowflake is a powerful engine, but it doesn't have a conscience. It will consume every credit you give it. By controlling your `AUTO_SUSPEND`, right-sizing your warehouses based on spill metrics, and enforcing strict clustering, you can easily cut your compute costs by 30-40% without sacrificing performance.

It takes five minutes to check these settings. It takes five hours to explain to your boss why the bill doubled. Choose wisely.

**Try it:** Run the spill query in Step 2 right now. Find your top three most expensive queries from the last week and see if they’re spilling to remote storage. If they are, you’ve found your first target for optimization.

***

**SEO keywords:** snowflake compute costs, snowflake optimization, sql warehouse tuning, cloud cost management
**Tags:** #snowflake #dataengineering #cloudcosts #sql

*Cover photo by [Taylor Vick](https://unsplash.com/@tvick?utm_source=articles_pipeline&utm_medium=referral) on [Unsplash](https://unsplash.com/?utm_source=articles_pipeline&utm_medium=referral).*
