import multiprocessing
import psutil
from app.core.log import LOG_FILE_PATH

cpu_count = multiprocessing.cpu_count()
total_mem_mb = psutil.virtual_memory().total // (1024 ** 2) # Memory in MB

# Estimate memory used per worker
MEMORY_PER_WORKER = 300    # Assume each worker uses ~300MB
MAX_WORKERS_BY_MEMORY = total_mem_mb // MEMORY_PER_WORKER
MAX_WORKERS_BY_CPU = 2 * cpu_count + 1
UPPER_CAP_WORKERS = 4 # (capped to 4 for safety)

workers = min(MAX_WORKERS_BY_CPU, MAX_WORKERS_BY_MEMORY, UPPER_CAP_WORKERS) 

worker_class = "uvicorn.workers.UvicornWorker"
bind = "0.0.0.0:5006"
timeout = 120

keepalive = 5
loglevel = "info"

# Logging configuration
accesslog = LOG_FILE_PATH      
errorlog = LOG_FILE_PATH    
capture_output = True