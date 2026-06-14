---
title: "Text-to-SQL is a solved problem: why you’re about to leak your PII"
published: false
description: "Stop treating LLMs like database admins. Here is how to build production-safe text-to-SQL over governed lakehouses without losing your job."
tags: sql, llm, security, data
cover_image: https://images.unsplash.com/photo-1544197150-b99a580bb7a8?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5NzU0MjJ8MHwxfHNlYXJjaHwzfHxzZXJ2ZXIlMjByYWNrJTIwY2FibGluZ3xlbnwwfDB8fHwxNzgxNDY0MDY2fDA&ixlib=rb-4.1.0&q=80&w=1080
canonical_url:
---

The most dangerous myth in modern data engineering is that "Text-to-SQL is a solved problem." Every time I see a demo where someone asks an LLM to "sum the revenue by region" and it returns a clean JSON blob, I see a production outage waiting to happen. You aren't building a chat interface; you are building a high-velocity, non-deterministic SQL generator that has direct access to your internal PII and financial ledger.

> **Why I chose this topic:** I spent three months cleaning up after a "smart agent" that hallucinated a `DROP TABLE` command because a user asked it to "clear out the old test data." In high-stakes environments like healthcare, "oops, the LLM got confused" isn't a valid root cause analysis for a HIPAA breach.

You are currently facing a binary choice: either you wrap your data in a brittle, manual semantic layer, or you embrace a "governed lakehouse" architecture where the LLM is treated as an untrusted, low-privilege user. Most people choose the latter because they are lazy, and then they end up crying when the model joins the `users` table to the `audit_logs` table via a natural language prompt.

## The contenders

You have three ways to approach this. First, the **"LLM-as-a-DBA"** approach, where you feed the entire schema (Ddl) into the context window and pray for the best. Second, the **"Semantic Proxy"** layer, where you use tools like LlamaIndex or LangChain to map natural language to pre-defined view objects rather than raw tables. Third, the **"Hard-Gated SQL Sandbox"**, where the LLM generates SQL, but a deterministic Python validator checks the AST (Abstract Syntax Tree) before it ever touches a connection string.

![Photo by CHUTTERSNAP on Unsplash](https://images.unsplash.com/photo-1513082325166-c105b20374bb?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5NzU0MjJ8MHwxfHNlYXJjaHwxNnx8YnJva2VuJTIwZ2xhc3N8ZW58MHwwfHx8MTc4MTQ2NDA2Nnww&ixlib=rb-4.1.0&q=80&w=1080)
*Photo by [CHUTTERSNAP](https://unsplash.com/@chuttersnap?utm_source=articles_pipeline&utm_medium=referral) on [Unsplash](https://unsplash.com/?utm_source=articles_pipeline&utm_medium=referral)*


## The operational burden of hallucinations

The "LLM-as-a-DBA" approach is the cheapest way to start but the most expensive to run. If your schema has 500+ tables—standard for any mature lakehouse—you are going to blow your context window budget immediately. Even with GPT-4o, the "schema stuffing" method fails when you have columns with similar names like `user_id` in five different tables. 

I’ve seen models join `billing.payments` with `marketing.leads` on `id` just because they happened to have the same name. In a production environment, this is a silent data corruption error. If you aren't using a tool that enforces table lineage at the semantic level (like dbt’s graph metadata), you are just running an expensive game of SQL Roulette.

## Infrastructure and cost at scale

Running a SQL agent against a Delta Lake or Iceberg table isn't just about the token cost. It’s about the compute cost. If your agent is allowed to write `SELECT *` without limits, you will incinerate your Snowflake or Databricks budget in an afternoon. 

I implement a mandatory `LIMIT 1000` hard-coded into the system prompt, but that’s not enough. You need to enforce a resource governor at the database level. For Databricks, I use a specific `SQL Warehouse` policy that caps the DBU usage per session. If the LLM generates a query that hits a massive fact table without a partition key, the warehouse should kill the query within 30 seconds. If your agent doesn't have a "kill switch" policy, you aren't ready for production.

![Photo by MJ Duford on Unsplash](https://images.unsplash.com/photo-1743796055664-3473eedab36e?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5NzU0MjJ8MHwxfHNlYXJjaHwzfHxtYWduaWZ5aW5nJTIwZ2xhc3MlMjBvbiUyMGNvZGV8ZW58MHwwfHx8MTc4MTQ2NDA2N3ww&ixlib=rb-4.1.0&q=80&w=1080)
*Photo by [MJ Duford](https://unsplash.com/@duforddigital?utm_source=articles_pipeline&utm_medium=referral) on [Unsplash](https://unsplash.com/?utm_source=articles_pipeline&utm_medium=referral)*


## Failure modes and the AST validator

The most common failure mode isn't a bad query; it’s an unauthorized one. LLMs love to "explore." If a user asks, "Who are our highest-paying customers?", the model might decide it needs to look at the `sensitive_user_pii` table to provide a better answer. 

You cannot trust the model to self-regulate. You need an AST validator. I use the `sqlglot` library in Python to parse every generated query. If the AST contains a `JOIN` to a table not in the allowed list, or if it tries to access a `WHERE` clause containing `email` or `ssn`, the validator throws a hard exception. 

Here is what that looks like in practice:
```python
import sqlglot
from sqlglot import exp

def validate_query(sql_str):
    parsed = sqlglot.parse_one(sql_str)
    for table in parsed.find_all(exp.Table):
        if table.name in ['PII_TABLE', 'CREDIT_CARDS']:
            raise PermissionError("Access denied to sensitive table.")
    return True
```
If you aren't doing this, you are effectively letting users run arbitrary code on your backend.

## What I'd pick, and why

I pick the "Hard-Gated SQL Sandbox" every time. It’s annoying to set up, it requires keeping a registry of "allowed tables," and it slows down the initial development cycle. But it’s the only way to sleep at night.

Here is the stack I recommend:
1. **Semantic Layer:** Use `dbt` to define your models. The LLM shouldn't know about your raw Bronze/Silver/Gold tables; it should only see the Gold-level models you’ve explicitly exposed.
2. **Validator:** Use `sqlglot` to audit the AST. Block any query that doesn't explicitly mention a partition column if the table is over a certain size (like `event_date`).
3. **Execution:** Use a low-privilege service account for the LLM. If your agent is running as a `SUPERUSER`, you’ve already lost. Use `GRANT SELECT` only on the specific views the agent is allowed to touch.

The caveat? It makes the "smart" agent feel a bit "dumb." Users will occasionally get a "I cannot answer that" response because the agent was blocked by the validator. This is a feature, not a bug. Your users will be annoyed that they can't ask the agent for the CEO’s home address, but your CISO will be thrilled. In financial services, the "I don't know" response is the mark of a system that is actually under control. 

Stop trying to build an omniscient SQL wizard. Build a constrained, grumpy librarian that only gives out the books it’s allowed to touch. That’s how you ship in production.

*Cover photo by [Jordan Harrison](https://unsplash.com/@jouwdan?utm_source=articles_pipeline&utm_medium=referral) on [Unsplash](https://unsplash.com/?utm_source=articles_pipeline&utm_medium=referral).*
