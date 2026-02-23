/******************************************************************************
 * temporal_contact_matrix.h
 *
 * Public header exposing the data structures and functions from
 * "temporal_contact_matrix.c" for use by other modules.
 *
 * Usage:
 *   #include "temporal_contact_matrix.h"
 *   Link with temporal_contact_matrix.c and intervaldb.c (plus libxml2).
 ******************************************************************************/

#ifndef TEMPORAL_CONTACT_MATRIX_H
#define TEMPORAL_CONTACT_MATRIX_H

#ifdef _WIN32
  #define DLL_EXPORT __declspec(dllexport)
#else
  #define DLL_EXPORT
#endif


#include <stdint.h>

#include "uthash.h"

#include "intervaldb.h" /* for IntervalMap, SublistHeader, etc. */

/* --------------------------------------------------------------------------
 * Data structures from the original temporal_contact_matrix.c
 * -------------------------------------------------------------------------- */

/* The node map is used internally in temporal_contact_matrix.c. */
typedef struct {
  char *node_id;     /* original string from graph */
  int index;         /* compressed integer index */
  UT_hash_handle hh; /* uthash handle for fast lookup */
} NodeItem;

/* The IntervalMap is defined in intervaldb.h, but typically like:
 *
 * typedef struct IntervalMap {
 *     int64_t start;
 *     int64_t end;
 *     int64_t target_id;
 *     int     sublist;
 * } IntervalMap;
 *
 * We do not redefine it if intervaldb.h does so. Just be sure the types match.
 */

/* For storing the final nested list metadata */
typedef struct {
  IntervalMap *im;
  SublistHeader *subheader;
  int n;      /* number of intervals after compression */
  int nlists; /* number of sublists */
} IntervalDBWrapper;

/* Sparse edge output tuple: for sparse representation of the adjacency matrix */
typedef struct {
  int src;
  int tgt;
  float weight;
} EdgeTuple;

/* --------------------------------------------------------------------------
 * Public function declarations
 * -------------------------------------------------------------------------- */

/* pack_node_pair / unpack_node_pair
 *   Combine or split (src, tgt) into a single 64-bit integer.
 */
int64_t pack_node_pair(int src, int tgt);
void unpack_node_pair(int64_t packed, int *src, int *tgt);

/* parse_edgelist_build_intervals:
 *   Reads the new NODE_LIST format and returns an IntervalMap array.
 *   Caller must free the returned array.
 */
IntervalMap *parse_edgelist_build_intervals(const char *filename, int *p_count);

/* Count valid edge lines (helper used internally) */
int count_valid_edges(const char *filename);

/* build_interval_db_wrapper:
 *   Takes the intervals, builds the nested list DB.
 *   Returns 1 on success, 0 on failure.
 */
int build_interval_db_wrapper(IntervalMap *im, int n, IntervalDBWrapper *dbw);

/* free_interval_db_wrapper:
 *   Cleans up resources in the wrapper.
 */
void free_interval_db_wrapper(IntervalDBWrapper *dbw);

/* free_node_map:
 *   Frees the hash-based node map used internally for ID -> index compression.
 */
void free_node_map(void);

/* Convert ISO8601 string to Unix timestamp (UTC). */
int64_t parse_iso8601_to_unix(const char *time_str);

/* Return a unique compressed index for a node string (or create one). */
int get_or_create_node_index(const char *node_id_str);

/* --------------------------------------------------------------------------
 * Python-facing API (used via ctypes or external modules)
 * -------------------------------------------------------------------------- */

#ifdef __cplusplus
extern "C" {
#endif

/**
 * init_temporal_db:
 *   Initializes the interval database and node map from a contact file.
 *   Should be called once before any queries.
 * 
 * @param filename Path to the contact file with NODE_LIST and edges
 * @return 1 on success, 0 on failure
 */
DLL_EXPORT int init_temporal_db(const char *filename);

/**
 * set_exit_node_label:
 *   Sets the node label (string) that should be treated as the EXIT node.
 *   Any edges incident to the EXIT node will be filtered out in window queries.
 *
 *   Notes:
 *   - The label is compared against the *internal node ID strings* stored in the
 *     node map. In your current parser, node IDs are read as integers and then
 *     converted to strings (e.g. 42 -> "42"). So an exit label like "EXIT" will
 *     only match if your node IDs are literally "EXIT" (not possible with %d).
 *     If you want to exclude a numeric node, pass its string form (e.g. "42").
 *   - Safe to call before or after init_temporal_db(). If called after init,
 *     the cached EXIT index will be refreshed immediately.
 *
 * @param label  Null-terminated label string (leading/trailing whitespace trimmed).
 * @return       1 on success, 0 on invalid input (NULL/empty/too long).
 */
DLL_EXPORT int set_exit_node_label(const char *label);

/**
 * get_adjacency_matrix_for_window:
 *   Fills the provided out_matrix buffer (size N*N floats) with
 *   contact durations between nodes that overlap the [start, end) time window.
 * 
 * @param start      Start time (Unix timestamp, inclusive)
 * @param end        End time (Unix timestamp, exclusive)
 * @param out_matrix Caller-allocated buffer of size N*N (row-major)
 * @return 1 on success, 0 on failure
 */
DLL_EXPORT int get_adjacency_matrix_for_window(int64_t start, int64_t end, float *out_matrix);

/**
 * get_edge_list_for_window:
 *   Returns a sparse edge list of node pairs (src, tgt, weight) overlapping
 *   the given time window. The caller must allocate an EdgeTuple buffer
 *   of at least max_edges capacity.
 *
 * @param start       Start time (Unix timestamp)
 * @param end         End time (Unix timestamp)
 * @param edges_out   Pointer to preallocated EdgeTuple array
 * @param max_edges   Maximum number of EdgeTuple entries that can be written
 * @return            Number of edges written, or -1 if the buffer was too small / not initialized
 */
DLL_EXPORT int get_edge_list_for_window(int64_t start, int64_t end,
                                        EdgeTuple *edges_out, int max_edges);


/**
 * shutdown_temporal_db:
 *   Frees all internal resources (interval map, node map, etc).
 *   Call this before exiting your program to avoid memory leaks.
 */
DLL_EXPORT void shutdown_temporal_db(void);

/* Return the total number of nodes specified by NODE_LIST= */
DLL_EXPORT int get_total_node_count(void);

#ifdef __cplusplus
}
#endif

#endif /* TEMPORAL_CONTACT_MATRIX_H */
