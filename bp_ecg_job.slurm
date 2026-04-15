#!/bin/bash
# bp_ecg batch processor — SLURM manager job
#
# This script runs on a single node with minimal resources.
# It starts a Dask SLURMCluster which automatically submits worker jobs
# across the cluster (n01, n02, n03) via dask-jobqueue.
#
# Usage:
#   sbatch bp_ecg_job.slurm
#
# Override defaults via environment:
#   INPUT_DIRECTORY=... OUTPUT_DIRECTORY=... sbatch bp_ecg_job.slurm

#SBATCH --job-name=bp_ecg_manager
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G
#SBATCH --time=48:00:00
#SBATCH --output=logs/bp_ecg_%j.out
#SBATCH --error=logs/bp_ecg_%j.err

set -euo pipefail
mkdir -p logs

# ---------------------------------------------------------------------------
# Environment activation
# ---------------------------------------------------------------------------
conda activate bp_ecg

# ---------------------------------------------------------------------------
# Redis — start on this (manager) node so workers can connect
# All SLURM worker jobs inherit the environment via --export=ALL,
# so REDIS_URL is automatically visible to dask-jobqueue workers.
# ---------------------------------------------------------------------------
REDIS_PORT=16379   # non-standard port to avoid conflicts
redis-server \
    --daemonize yes \
    --port "${REDIS_PORT}" \
    --loglevel warning \
    --save ""          # disable RDB snapshots (ephemeral, in-memory only)

export REDIS_URL="redis://$(hostname -s):${REDIS_PORT}"
echo "Redis started at ${REDIS_URL}"

# ---------------------------------------------------------------------------
# Batch job configuration
# ---------------------------------------------------------------------------
export INPUT_DIRECTORY="${INPUT_DIRECTORY:-/home/$USER/data/incoming}"
export OUTPUT_DIRECTORY="${OUTPUT_DIRECTORY:-/home/$USER/data/output}"
export N_WORKERS="${N_WORKERS:-24}"
export CORES_PER_WORKER="${CORES_PER_WORKER:-8}"
export MEM_PER_WORKER_GB="${MEM_PER_WORKER_GB:-16}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
export ENVIRONMENT="${ENVIRONMENT:-prod}"

mkdir -p "${OUTPUT_DIRECTORY}"

echo "=============================================="
echo "bp_ecg batch processor — SLURM job ${SLURM_JOB_ID}"
echo "  Redis      : ${REDIS_URL}"
echo "  Input dir  : ${INPUT_DIRECTORY}"
echo "  Output dir : ${OUTPUT_DIRECTORY}"
echo "  Workers    : ${N_WORKERS} × ${CORES_PER_WORKER} cores / ${MEM_PER_WORKER_GB}GB"
echo "=============================================="

# ---------------------------------------------------------------------------
# Run — SLURMCluster is spawned inside build_client() because SLURM_JOB_ID
# is set, so worker jobs are submitted automatically.
# ---------------------------------------------------------------------------
python -m bp_ecg_watcher.main

# ---------------------------------------------------------------------------
# Shutdown Redis cleanly
# ---------------------------------------------------------------------------
redis-cli -p "${REDIS_PORT}" shutdown nosave 2>/dev/null || true
echo "Redis stopped."
