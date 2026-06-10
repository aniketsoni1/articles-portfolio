---
title: "It Works on My Cluster: Containerizing Spark and Lakehouse Development with Docker"
published: false
description: "How to use Docker to give every data engineer a laptop-sized replica of a production lakehouse — local Spark, Delta Lake, orchestration, and CI that actually matches prod."
tags: docker, dataengineering, spark, devops
cover_image: https://raw.githubusercontent.com/aniketsoni1/articles-html-portfolio/main/articles/article06102026_021207/cover.png
---

> **Why I chose this topic:** Most Docker content targets web developers shipping stateless services. Data engineers — a huge and growing population of Docker users — are mostly left to figure things out alone, and it shows: pipelines that pass locally and explode on the cluster, "notebook-only" development against expensive cloud workspaces, and CI suites that mock Spark instead of running it. This article applies six years of production data platform experience in financial services and healthcare to a question almost nobody answers well: *how do you make a laptop behave like a lakehouse?*

If you build data pipelines for a living, you've lived this story. Your PySpark job runs perfectly in a cloud notebook. You productionize it, push it through CI, deploy it to the cluster — and it fails. A dependency mismatch. A different Spark minor version. A Delta Lake protocol feature your local wheel doesn't know about. A timezone default nobody set.

Web developers solved "works on my machine" a decade ago with containers. Data engineers, somehow, are still developing against shared cloud workspaces, paying per-minute cluster costs to debug a `GROUP BY`, and discovering environment drift in production.

This article is the workflow I wish someone had handed me years ago: a fully containerized lakehouse development environment — Spark, Delta Lake, object storage, a catalog, and orchestration — that runs on a laptop, mirrors production closely enough to trust, and plugs into CI without mocks.

## The real problem: data pipelines have *four* environments, not one

A typical stateless web service has one environment to reproduce: the app runtime. A data pipeline has at least four, and they drift independently:

1. **The compute runtime** — Spark version, Scala version, JVM, Python, native libs (Arrow, Parquet, libhdfs).
2. **The table format layer** — Delta Lake / Iceberg versions and *protocol* versions, which are not the same thing.
3. **The storage layer** — S3/ADLS semantics: multipart uploads, eventual consistency quirks, path-style vs virtual-hosted access.
4. **The orchestration layer** — the scheduler's Python environment, which is famously *not* your job's environment.

Mocking any one of these in tests means you aren't testing the thing that breaks. The goal of containerizing a lakehouse is to pin all four layers in code and version them together.

![The Four-Layer Drift Problem: compute, table format, storage, and orchestration drift independently between laptop and production; containerizing pins all four together.](https://raw.githubusercontent.com/aniketsoni1/articles-html-portfolio/main/articles/article06102026_021207/drift.png)

## Step 1: A reproducible Spark image you actually control

Don't develop against `latest`. Build a base image that pins every layer of the compute runtime and treat it like an artifact:

```dockerfile
# syntax=docker/dockerfile:1.7
FROM eclipse-temurin:17-jre-jammy AS base

ARG SPARK_VERSION=3.5.4
ARG DELTA_VERSION=3.3.0
ARG HADOOP_AWS_VERSION=3.3.6

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3.11 python3-pip tini && \
    rm -rf /var/lib/apt/lists/*

# Pin Spark itself, not just PySpark
RUN curl -fsSL https://archive.apache.org/dist/spark/spark-${SPARK_VERSION}/spark-${SPARK_VERSION}-bin-hadoop3.tgz \
    | tar -xz -C /opt && mv /opt/spark-${SPARK_VERSION}-bin-hadoop3 /opt/spark

ENV SPARK_HOME=/opt/spark PATH=$PATH:/opt/spark/bin PYTHONHASHSEED=0 TZ=UTC

# Delta + S3 connectors resolved at build time, never at job submit time
RUN /opt/spark/bin/spark-shell --packages \
      io.delta:delta-spark_2.12:${DELTA_VERSION},org.apache.hadoop:hadoop-aws:${HADOOP_AWS_VERSION} \
      -e "println(\"deps cached\")" && \
    cp /root/.ivy2/jars/*.jar /opt/spark/jars/

COPY requirements.lock /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.lock

# Never run Spark as root
RUN useradd -m -u 1001 spark
USER 1001
ENTRYPOINT ["/usr/bin/tini", "--"]
```

Three details that matter more than they look:

- **`--packages` at build time, not submit time.** Resolving connector JARs at `spark-submit` is the #1 source of "it worked yesterday" failures — Maven Central is a runtime dependency you didn't mean to have.
- **`PYTHONHASHSEED=0` and `TZ=UTC`** kill two classes of "non-deterministic only in prod" bugs.
- **A lockfile, not `requirements.txt`.** Compile with `pip-compile` or `uv pip compile` so transitive dependencies (looking at you, `pandas`/`pyarrow`) can't drift.

## Step 2: The lakehouse-in-a-box with Docker Compose

Here's the part most teams never build: the *rest* of the lakehouse, locally. MinIO stands in for S3 (it speaks the same API), and a real Spark master/worker pair stands in for the cluster — because `local[*]` mode hides every serialization and shuffle bug you'll meet in production.

```yaml
# compose.yaml
services:
  spark-master:
    build: .
    command: /opt/spark/sbin/start-master.sh
    environment: [SPARK_NO_DAEMONIZE=true]
    ports: ["7077:7077", "8080:8080"]

  spark-worker:
    build: .
    command: /opt/spark/sbin/start-worker.sh spark://spark-master:7077
    environment:
      - SPARK_NO_DAEMONIZE=true
      - SPARK_WORKER_MEMORY=4g
      - SPARK_WORKER_CORES=2
    depends_on: [spark-master]
    deploy:
      replicas: 2          # >1 worker = real shuffles, real serialization

  minio:
    image: minio/minio:RELEASE.2025-09-07T16-13-09Z
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: localdev
      MINIO_ROOT_PASSWORD: localdev-secret
    ports: ["9000:9000", "9001:9001"]
    volumes: [lake-data:/data]
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s

  mc-init:                  # create the bronze/silver/gold buckets on boot
    image: minio/mc:latest
    depends_on: { minio: { condition: service_healthy } }
    entrypoint: >
      /bin/sh -c "mc alias set local http://minio:9000 localdev localdev-secret &&
      mc mb -p local/lakehouse/bronze local/lakehouse/silver local/lakehouse/gold"

volumes:
  lake-data:
```

Point Spark at MinIO with three config lines and your medallion pipeline reads and writes `s3a://lakehouse/...` paths exactly like production:

```python
spark = (SparkSession.builder
    .config("spark.hadoop.fs.s3a.endpoint", "http://minio:9000")
    .config("spark.hadoop.fs.s3a.path.style.access", "true")
    .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
    .config("spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog")
    .getOrCreate())
```

`docker compose up` and you have bronze → silver → gold on your laptop. Total cloud cost of a debugging session: $0.

![Lakehouse in a Box: a Docker Compose project with spark-master, two spark-workers, MinIO (bronze/silver/gold buckets) and Airflow, with a developer laptop and CI runner both pointing at the same Compose file.](https://raw.githubusercontent.com/aniketsoni1/articles-html-portfolio/main/articles/article06102026_021207/lakehouse.png)

## Step 3: Integration tests that run real Spark — Testcontainers

The payoff of all this is CI you can trust. With Testcontainers, your pipeline tests spin up the *same* images your developers use:

```python
import pytest
from testcontainers.minio import MinioContainer
from pyspark.sql import SparkSession

@pytest.fixture(scope="session")
def lake(request):
    with MinioContainer("minio/minio:RELEASE.2025-09-07T16-13-09Z") as minio:
        yield minio

def test_silver_dedup_keeps_latest_record(lake, spark):
    # write duplicate customer events to bronze
    bronze_path = f"s3a://test/bronze/customers"
    write_fixture_events(spark, bronze_path, duplicates=True)

    run_silver_dedup(spark, bronze_path, "s3a://test/silver/customers")

    result = spark.read.format("delta").load("s3a://test/silver/customers")
    assert result.count() == EXPECTED_UNIQUE
    assert latest_record_wins(result)
```

No mocked DataFrames. No `unittest.mock.patch("boto3...")`. The test exercises Delta's actual transaction log against actual object storage. When this suite is green, deployments stop being scary.

A pattern I use in regulated environments: keep a `fixtures/` directory of small, *synthetic* Parquet files that mirror production schemas (never production data), and version them with the code. Schema drift then fails a unit test instead of a 2 a.m. pipeline run.

## Step 4: One image from laptop → CI → production

The final principle: **the image you test is the artifact you ship.** Multi-stage builds let one Dockerfile serve dev (with Jupyter, debuggers) and prod (minimal, non-root):

```dockerfile
FROM base AS dev
USER root
RUN pip install --no-cache-dir jupyterlab pytest debugpy
USER 1001

FROM base AS prod
COPY --chown=1001:1001 src/ /app/src/
COPY --chown=1001:1001 jobs/ /app/jobs/
# nothing else — no notebooks, no test deps, no shell tools you don't need
```

In CI: build once, tag with the git SHA, run the Testcontainers suite against `prod`, scan it (Docker Scout, or your registry's scanner), sign it, and promote *that exact digest* through staging to the scheduler. Whether the scheduler is Airflow's `DockerOperator`/`KubernetesPodExecutor` or a managed Spark platform pulling custom containers, the principle holds: environments are immutable, versioned, and identical by construction.

## Lessons learned from production

- **Run ≥2 workers locally.** `local[*]` mode never serializes between JVMs. The day you switch to a real cluster, every closure-capture and UDF-pickling bug appears at once. Two 2-core workers in Compose surfaces them on day one.
- **Pin the table format protocol, not just the library.** Delta and Iceberg both evolve table *protocol* versions. A newer writer can produce tables an older reader can't open. Encode the protocol version in your image build args and test reads with the oldest reader you support.
- **MinIO is a stand-in, not a clone.** It won't reproduce S3 request throttling or cross-region latency. Keep a small smoke-test suite that runs against real object storage nightly; do everything else locally.
- **Resource-limit your local Spark.** Without `SPARK_WORKER_MEMORY` caps, a skewed join will cheerfully eat your laptop. Limits also force you to think about partitioning early — which is the point.
- **Treat the orchestrator's image as layer four.** Airflow DAG-parse environments drift too. Containerize the scheduler with the same lockfile discipline as the jobs.

## Production considerations

Before you take this pattern to a real platform team, three things to plan for: **secrets** (local Compose uses throwaway creds; production should inject via your cloud's secret manager or Docker secrets — never baked into images), **image provenance** (sign images and generate SBOMs in CI; regulated industries will ask, and in 2026 the tooling is mature enough that "we didn't get to it" no longer flies), and **base image hygiene** (start from minimal, hardened bases and rebuild on a schedule, not just on code change — CVEs don't wait for your sprint).

## Conclusion

Containers gave application developers reproducibility ten years ago. Data engineering is finally having the same moment — and the teams that containerize their lakehouse development loop ship faster, test honestly, and stop paying cloud bills to find typos.

**Try it:** clone the Compose stack above, point your gnarliest pipeline at it, and see what breaks locally that used to break in prod. Then tell me about it — I'd genuinely like to hear which layer drifted on you.

If this was useful, follow me here and on LinkedIn — next up in this series: load-testing Delta merge performance locally, and contract testing between pipeline stages.

---

**SEO keywords:** Docker for data engineering, containerized Spark development, Delta Lake Docker, local lakehouse, Spark Docker Compose, Testcontainers PySpark, MinIO Spark, reproducible data pipelines, data engineering CI/CD, medallion architecture Docker.

**Tags:** #docker #dataengineering #spark #deltalake #devops
