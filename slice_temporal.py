import ctypes
import numpy as np
import os
from scipy import sparse
from scipy.sparse.csgraph import connected_components

# Optional imports for additional sparse stats
try:
    import networkx as nx
except ImportError:
    nx = None  # We'll check before using

# -------------------------------------------------------------------
# FFI Binding to temporal_contact_matrix.{dll/so/dylib}
# -------------------------------------------------------------------

# Load the dynamic library (DLL/SO/DYLIB)
LIB_PATH = os.path.join(os.path.dirname(__file__), "temporal_contact_matrix/temporal_contact_matrix.dll")
lib = ctypes.CDLL(LIB_PATH)

# Define ctypes signatures
lib.init_temporal_db.argtypes = [ctypes.c_char_p]
lib.init_temporal_db.restype = ctypes.c_int

lib.get_total_node_count.argtypes = []
lib.get_total_node_count.restype = ctypes.c_int

lib.get_adjacency_matrix_for_window.argtypes = [
    ctypes.c_int64,
    ctypes.c_int64,
    ctypes.POINTER(ctypes.c_float),
]
lib.get_adjacency_matrix_for_window.restype = ctypes.c_int

lib.shutdown_temporal_db.argtypes = []
lib.shutdown_temporal_db.restype = None


# -------------------------------------------------------------------
# Adjacency Matrix Wrapper
# -------------------------------------------------------------------

class AdjacencyMatrixGenerator:
    def __init__(self, filename: str):
        if not lib.init_temporal_db(filename.encode("utf-8")):
            raise RuntimeError("Failed to initialize temporal DB.")
        self.num_nodes = lib.get_total_node_count()
        print(f"[DEBUG] Temporal DB loaded with {self.num_nodes} nodes.")

    def query(self, start_ts: int, end_ts: int, directed: bool = False):
        N = self.num_nodes
        buffer = (ctypes.c_float * (N * N))()

        if not lib.get_adjacency_matrix_for_window(start_ts, end_ts, buffer):
            raise RuntimeError("Failed to query adjacency matrix.")

        # Dense to CSR conversion
        dense = np.ctypeslib.as_array(buffer).reshape((N, N))

        if not directed:
            dense = np.maximum(dense, dense.T)

        # Diagonal indicates presence in hospital
        in_hospital = np.diag(dense) > 0

        # Build CSR sparse matrix
        sparse_mat = sparse.csr_matrix(dense)

        return sparse_mat, in_hospital

    def shutdown(self):
        lib.shutdown_temporal_db()

    def __del__(self):
        try:
            self.shutdown()
        except Exception:
            pass


# -------------------------------------------------------------------
# Utility Functions for Sparse Network Analysis
# -------------------------------------------------------------------

def get_sparse_stats(sparse_mat: sparse.csr_matrix, verbose: bool = True):
    stats = {}

    if verbose:
        print(f"[INFO] Matrix shape: {sparse_mat.shape}, nnz: {sparse_mat.nnz}")
        print(f"[INFO] Density: {sparse_mat.nnz / (sparse_mat.shape[0] ** 2):.6f}")

    # Degree (in + out)
    degrees = np.array(sparse_mat.sum(axis=1)).flatten()
    stats['degree_min'] = degrees.min()
    stats['degree_max'] = degrees.max()
    stats['degree_mean'] = degrees.mean()

    if verbose:
        print(f"[INFO] Degree: min={stats['degree_min']}, max={stats['degree_max']}, mean={stats['degree_mean']:.2f}")

    # Connected components (undirected)
    n_components, labels = connected_components(sparse_mat, directed=False)
    stats['connected_components'] = n_components

    if verbose:
        print(f"[INFO] Connected components: {n_components}")

    return stats


# -------------------------------------------------------------------
# Example CLI Usage
# -------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Slice temporal contact network.")
    parser.add_argument("graph_file", help="Path to .txt/.bin graph file")
    parser.add_argument("--start", type=int, default=946713600, help="Start time (epoch)")
    parser.add_argument("--end", type=int, default=946713600 + 3600, help="End time (epoch)")
    parser.add_argument("--directed", action="store_true", help="Keep directed edges")
    parser.add_argument("--no-stats", action="store_true", help="Skip printing network stats")
    args = parser.parse_args()

    gen = AdjacencyMatrixGenerator(args.graph_file)
    adj, hospital = gen.query(args.start, args.end, directed=args.directed)

    print(f"[INFO] Sparse adjacency matrix: shape={adj.shape}, nnz={adj.nnz}")
    print(f"[INFO] Nodes present in hospital: {np.nonzero(hospital)[0].tolist()}")

    if not args.no_stats:
        get_sparse_stats(adj)

    gen.shutdown()


if __name__ == "__main__":
    main()
