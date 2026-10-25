"""Single-user local knowledge service; scoped RAGFlow compatibility."""
import os

# Apply before NumPy/FAISS load. omp_set_num_threads in store.py affects the
# calling thread; FastAPI workers also need a bounded default from the environment.
# Small local exact indices do not benefit from large BLAS/OpenMP thread pools.
for _variable in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_variable] = '1'
