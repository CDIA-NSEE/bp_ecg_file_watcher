#!/bin/bash
# bp_ecg batch processor — SLURM manager job
#
# This script runs on a single node with minimal resources.
# It starts a Dask SLURMCluster which automatically submits worker jobs
# across the cluster (n01, n02) via dask-jobqueue.
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
#SBATCH --partition=cpu
#SBATCH --output=logs/bp_ecg_%j.out
#SBATCH --error=logs/bp_ecg_%j.err

set -euo pipefail
mkdir -p logs

# ---------------------------------------------------------------------------
# Environment activation
# ---------------------------------------------------------------------------
source $HOME/miniconda3/bin/activate
conda activate bp_ecg

# ---------------------------------------------------------------------------
# Batch job configuration
# ---------------------------------------------------------------------------
export INPUT_DIRECTORY="${INPUT_DIRECTORY:-/home/$USER/data/incoming}"
export OUTPUT_DIRECTORY="${OUTPUT_DIRECTORY:-/home/$USER/data/output}"
export DEDUP_DIRECTORY="${DEDUP_DIRECTORY:-${OUTPUT_DIRECTORY}/.dedup}"
export N_WORKERS="${N_WORKERS:-256}"
export CORES_PER_WORKER="${CORES_PER_WORKER:-1}"
export MEM_PER_WORKER_GB="${MEM_PER_WORKER_GB:-2}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
export ENVIRONMENT="${ENVIRONMENT:-prod}"

mkdir -p "${OUTPUT_DIRECTORY}"
mkdir -p "${DEDUP_DIRECTORY}"

# Remove stale .pending locks left by any previously killed job.
# Safe on BeeGFS: all nodes share the same filesystem view.
find "${DEDUP_DIRECTORY}" -name '*.pending' -delete

echo "=============================================="
echo "bp_ecg batch processor — SLURM job ${SLURM_JOB_ID}"
echo "  Dedup dir  : ${DEDUP_DIRECTORY}"
echo "  Input dir  : ${INPUT_DIRECTORY}"
echo "  Output dir : ${OUTPUT_DIRECTORY}"
echo "  Workers    : ${N_WORKERS} × ${CORES_PER_WORKER} cores / ${MEM_PER_WORKER_GB}GB"
echo "=============================================="

# ---------------------------------------------------------------------------
# Run — SLURMCluster is spawned inside build_client() because SLURM_JOB_ID
# is set, so worker jobs are submitted automatically.
# ---------------------------------------------------------------------------
python -m bp_ecg_watcher.main
