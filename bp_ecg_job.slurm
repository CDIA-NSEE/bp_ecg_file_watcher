#!/bin/bash
# bp_ecg batch processor — SLURM manager job
#
# Submits 3 identical array jobs on n02 (192 cores total, 64 per job).
# Each job runs a Dask LocalCluster with 64 workers.
# The dedup store ensures no two jobs process the same file.
#
# Usage:
#   sbatch bp_ecg_job.slurm
#
# Override defaults via environment:
#   INPUT_DIRECTORY=... OUTPUT_DIRECTORY=... sbatch bp_ecg_job.slurm

#SBATCH --job-name=bp_ecg
#SBATCH --array=1-3
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --mem=120G
#SBATCH --time=48:00:00
#SBATCH --partition=cpu-9654
#SBATCH --nodelist=n02
#SBATCH --output=bp_ecg_%A_%a.out
#SBATCH --error=bp_ecg_%A_%a.err

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
export N_WORKERS="${N_WORKERS:-64}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
export ENVIRONMENT="${ENVIRONMENT:-prod}"

mkdir -p "${OUTPUT_DIRECTORY}"
mkdir -p "${DEDUP_DIRECTORY}"

# Remove stale .pending locks left by any previously killed job.
# Safe on BeeGFS: all nodes share the same filesystem view.
find "${DEDUP_DIRECTORY}" -name '*.pending' -delete

echo "=============================================="
echo "bp_ecg batch processor — array job ${SLURM_ARRAY_JOB_ID}, task ${SLURM_ARRAY_TASK_ID}"
echo "  Dedup dir  : ${DEDUP_DIRECTORY}"
echo "  Input dir  : ${INPUT_DIRECTORY}"
echo "  Output dir : ${OUTPUT_DIRECTORY}"
echo "  Workers    : ${N_WORKERS} (LocalCluster on n02)"
echo "=============================================="

# ---------------------------------------------------------------------------
# Run — unset SLURM_JOB_ID so dispatcher uses LocalCluster instead of
# SLURMCluster. Each array task runs 64 workers on its own 64 allocated cores.
# ---------------------------------------------------------------------------
unset SLURM_JOB_ID
python -m bp_ecg_watcher.main
