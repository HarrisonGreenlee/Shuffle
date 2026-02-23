#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <time.h>
#include <ctype.h>
#include <errno.h>
#include "uthash.h"
#include "intervaldb.h" // Required for NCLS

#define MAX_LINE 100000
// MAX_VISITS_PER_ROOM is no longer used as a fixed per-room array size.
// Keep it as an optional safety cap (set to 0 to disable).
#define MAX_VISITS_PER_ROOM 0
#define MAX_CONTACTS 10000000
#define MAX_PATIENTS 1000000

static char g_exit_token[64] = "EXIT";
static int64_t g_exit_gap_seconds = -1;  // <0 means disabled (no splitting)

typedef struct {
    int patient_id;
    time_t start;
    time_t end;
} Visit;

typedef struct RoomEntry {
    char room_id[64];

    // Dynamic visit storage (fixes OOM from huge fixed array per room)
    Visit *visits;
    int visit_count;
    int visit_cap;

    UT_hash_handle hh;
} RoomEntry;

static RoomEntry *room_map = NULL;
static bool patient_seen[MAX_PATIENTS] = {0};

static time_t parse_time(const char *str) {
    struct tm tm = {0};
    if (sscanf(str, "%d-%d-%d %d:%d:%d",
               &tm.tm_year, &tm.tm_mon, &tm.tm_mday,
               &tm.tm_hour, &tm.tm_min, &tm.tm_sec) != 6) {
        fprintf(stderr, "[ERROR] Failed to parse time string: '%s'\n", str);
        return (time_t)-1;
    }
    tm.tm_year -= 1900;
    tm.tm_mon -= 1;

#ifdef _WIN32
    return _mkgmtime(&tm);
#else
    return timegm(&tm);
#endif
}

static void format_time(time_t t, char *buf, size_t len) {
    struct tm *ptm = gmtime(&t);
    if (!ptm) {
        // Defensive fallback
        snprintf(buf, len, "1970-01-01T00:00:00Z");
        return;
    }
    strftime(buf, len, "%Y-%m-%dT%H:%M:%SZ", ptm);
}

static void rstrip(char *s) {
    size_t n = strlen(s);
    while (n > 0 && isspace((unsigned char)s[n - 1])) {
        s[--n] = '\0';
    }
}

static void ensure_visit_capacity(RoomEntry *entry) {
    if (entry->visit_count < entry->visit_cap) return;

    int newcap = (entry->visit_cap == 0) ? 64 : entry->visit_cap * 2;
    Visit *nv = (Visit *)realloc(entry->visits, (size_t)newcap * sizeof(Visit));
    if (!nv) {
        fprintf(stderr, "[FATAL] realloc failed growing visits for room %s (newcap=%d)\n",
                entry->room_id, newcap);
        exit(1);
    }
    entry->visits = nv;
    entry->visit_cap = newcap;
}

static void add_visit(const char *room, int patient_id, time_t start, time_t end) {
    RoomEntry *entry = NULL;
    HASH_FIND_STR(room_map, room, entry);
    if (!entry) {
        entry = (RoomEntry *)calloc(1, sizeof(RoomEntry));
        if (!entry) {
            fprintf(stderr, "[FATAL] Memory allocation failed for room entry.\n");
            exit(1);
        }
        strncpy(entry->room_id, room, sizeof(entry->room_id) - 1);
        entry->room_id[sizeof(entry->room_id) - 1] = '\0'; // ensure null-termination

        entry->visits = NULL;
        entry->visit_count = 0;
        entry->visit_cap = 0;

        HASH_ADD_STR(room_map, room_id, entry);
    }

#if MAX_VISITS_PER_ROOM > 0
    if (entry->visit_count >= MAX_VISITS_PER_ROOM) {
        fprintf(stderr, "[WARN] Room %s exceeded max visits (%d), skipping visit.\n",
                room, MAX_VISITS_PER_ROOM);
        return;
    }
#endif

    ensure_visit_capacity(entry);
    entry->visits[entry->visit_count++] = (Visit){patient_id, start, end};
}

// Original O(n^2) processing kept (commented out) for reference.
/*
static void process_room_quadratic(RoomEntry *entry, FILE *fp) {
    for (int i = 0; i < entry->visit_count; ++i) {
        for (int j = i + 1; j < entry->visit_count; ++j) {
            Visit vi = entry->visits[i];
            Visit vj = entry->visits[j];

            // Prevent self-contact
            if (vi.patient_id == vj.patient_id) continue;

            time_t latest_start = vi.start > vj.start ? vi.start : vj.start;
            time_t earliest_end = vi.end < vj.end ? vi.end : vj.end;
            if (latest_start < earliest_end) {
                char start_buf[32], end_buf[32];
                format_time(latest_start, start_buf, sizeof(start_buf));
                format_time(earliest_end, end_buf, sizeof(end_buf));
                fprintf(fp, "%d, %d, %s, %s;\n", vi.patient_id, vj.patient_id, start_buf, end_buf);
            }
        }
    }
}
*/

static void process_room(RoomEntry *entry, FILE *fp) {
    int n = entry->visit_count;
    if (n <= 1) return;

    // Build the IntervalMap
    IntervalMap *imap = interval_map_alloc(n);
    if (!imap) {
        fprintf(stderr, "[FATAL] interval_map_alloc failed for room %s (n=%d)\n", entry->room_id, n);
        exit(1);
    }

    for (int i = 0; i < n; ++i) {
        imap[i].start = (int64_t)entry->visits[i].start;
        imap[i].end = (int64_t)entry->visits[i].end;
        imap[i].target_id = entry->visits[i].patient_id;
        imap[i].sublist = -1;
    }

    // Build NCLS structures
    int n_compact = 0, n_sublists = 0;
    SublistHeader *subh = build_nested_list(imap, n, &n_compact, &n_sublists);
    if (!subh) {
        fprintf(stderr, "[FATAL] Failed to build NCLS for room %s\n", entry->room_id);
        free(imap);
        return;
    }

    IntervalIterator *it = interval_iterator_alloc();
    if (!it) {
        fprintf(stderr, "[FATAL] interval_iterator_alloc failed for room %s\n", entry->room_id);
        free(imap);
        free(subh);
        exit(1);
    }

    // Allocate output buffer sized to worst-case overlaps (<= n)
    int result_cap = n;
    IntervalMap *result_buf = (IntervalMap *)malloc((size_t)result_cap * sizeof(*result_buf));
    if (!result_buf) {
        fprintf(stderr, "[FATAL] malloc result_buf failed for room %s (cap=%d)\n", entry->room_id, result_cap);
        free_interval_iterator(it);
        free(imap);
        free(subh);
        exit(1);
    }

    int n_results = 0;
    IntervalIterator *it_out = NULL;

    for (int i = 0; i < n; ++i) {
        int64_t qs = imap[i].start;
        int64_t qe = imap[i].end;
        int pid_a = (int)imap[i].target_id;

        n_results = 0;
        find_intervals(it, qs, qe, imap, n_compact, subh, n_sublists,
                       result_buf, result_cap, &n_results, &it_out);

        // Defensive: if the library reports more than cap, clamp (and warn once)
        if (n_results > result_cap) {
            fprintf(stderr,
                    "[WARN] Room %s: find_intervals returned %d results > cap %d; truncating.\n",
                    entry->room_id, n_results, result_cap);
            n_results = result_cap;
        }

        for (int j = 0; j < n_results; ++j) {
            int pid_b = (int)result_buf[j].target_id;

            // Skip self and duplicates (enforce pid_a < pid_b for uniqueness)
            if (pid_a >= pid_b) continue;

            int64_t latest_start = qs > result_buf[j].start ? qs : result_buf[j].start;
            int64_t earliest_end = qe < result_buf[j].end ? qe : result_buf[j].end;

            if (latest_start < earliest_end) {
                char start_buf[32], end_buf[32];
                format_time((time_t)latest_start, start_buf, sizeof(start_buf));
                format_time((time_t)earliest_end, end_buf, sizeof(end_buf));
                fprintf(fp, "%d, %d, %s, %s;\n", pid_a, pid_b, start_buf, end_buf);
            }
        }
    }

    free(result_buf);
    free_interval_iterator(it);
    free(imap);
    free(subh);
}


static bool parse_int64_strict(const char *s, int64_t *out) {
    if (!s) return false;
    while (isspace((unsigned char)*s)) s++;
    if (*s == '\0') return false;

    errno = 0;
    char *end = NULL;
    long long v = strtoll(s, &end, 10);
    if (errno != 0) return false;
    if (end == s) return false;

    while (isspace((unsigned char)*end)) end++;
    if (*end != '\0') return false;

    *out = (int64_t)v;
    return true;
}

static void parse_input(const char *filename) {
    FILE *fp = fopen(filename, "r");
    if (!fp) {
        fprintf(stderr, "[FATAL] Could not open input file: %s\n", filename);
        exit(1);
    }

    char line[MAX_LINE];
    int patient_id = 0;

    while (fgets(line, sizeof(line), fp)) {
        if (patient_id >= MAX_PATIENTS) {
            fprintf(stderr, "[WARN] Exceeded max patients (%d), stopping.\n", MAX_PATIENTS);
            break;
        }

        // Mark the current episode/pseudo-patient as present (important when splitting mid-line)
        patient_seen[patient_id] = true;

        if (patient_id % 500 == 0) {
            printf("[DEBUG] Parsing patient %d...\n", patient_id);
        }

        char *tokens[MAX_LINE / 2];
        int token_count = 0;

        char *ptr = strtok(line, ",");
        while (ptr && token_count < (int)(MAX_LINE / 2)) {
            while (isspace((unsigned char)*ptr)) ptr++;
            rstrip(ptr);
            tokens[token_count++] = ptr;
            ptr = strtok(NULL, ",");
        }


        char *last_room = NULL;
        char *last_time = NULL;

        for (int i = 0; i + 1 < token_count; i += 2) {
            char *room = tokens[i];
            char *time_str = tokens[i + 1];

            char *prev_room = last_room;
            char *prev_time = last_time;

            // If previous token was EXIT and gap to this token is large, start a new episode here
            if (g_exit_gap_seconds >= 0 &&
                prev_room && prev_time &&
                strcmp(prev_room, g_exit_token) == 0) {

                time_t t_exit = parse_time(prev_time);
                time_t t_next = parse_time(time_str);

                if (t_exit != (time_t)-1 && t_next != (time_t)-1) {
                    int64_t gap = (int64_t)(t_next - t_exit);
                    if (gap > g_exit_gap_seconds) {
                        patient_id++;
                        if (patient_id >= MAX_PATIENTS) {
                            fprintf(stderr, "[WARN] Exceeded max patients (%d), stopping.\n", MAX_PATIENTS);
                            fclose(fp);
                            return;
                        }
                        patient_seen[patient_id] = true;

                        // Start new episode at current token: do not create a visit spanning the split
                        last_room = room;
                        last_time = time_str;
                        continue;
                    }
                }
            }

            // Add visit interval if neither side is the exit token
            if (prev_room && prev_time &&
                strcmp(prev_room, g_exit_token) != 0 &&
                strcmp(room, g_exit_token) != 0) {

                time_t start = parse_time(prev_time);
                time_t end = parse_time(time_str);

                if (start == (time_t)-1 || end == (time_t)-1) {
                    fprintf(stderr, "[WARN] Skipping invalid time span.\n");
                } else {
                    add_visit(prev_room, patient_id, start, end);
                }
            }

            last_room = room;
            last_time = time_str;
        }


        

        // advance to next baseline patient row
        patient_id++;
    }

    printf("[INFO] Parsed %d patients from input.\n", patient_id);
    fclose(fp);
}

static void usage_and_exit(const char *prog) {
    fprintf(stderr,
        "Usage: %s input.txt output.txt [--exit-token TOKEN] [--exit-gap-seconds SECONDS]\n"
        "  --exit-gap-seconds must be a positive integer (seconds). Omit it for no splitting.\n",
        prog
    );
    exit(1);
}

int main(int argc, char *argv[]) {
    if (argc < 3) {
        fprintf(stderr,
            "Usage: %s input.txt output.txt [--exit-token TOKEN] [--exit-gap-seconds SECONDS]\n", argv[0]);
        return 1;
    }

    const char *input = argv[1];
    const char *output = argv[2];

    for (int i = 3; i < argc; i++) {
        if (strcmp(argv[i], "--exit-token") == 0 && i + 1 < argc) {
            strncpy(g_exit_token, argv[i + 1], sizeof(g_exit_token) - 1);
            g_exit_token[sizeof(g_exit_token) - 1] = '\0';
            i++;

            if (g_exit_token[0] == '\0') {
                fprintf(stderr, "[ERROR] --exit-token cannot be empty.\n");
                usage_and_exit(argv[0]);
            }
        } else if (strcmp(argv[i], "--exit-gap-seconds") == 0) {
            if (i + 1 >= argc) {
                fprintf(stderr, "[ERROR] --exit-gap-seconds requires a value.\n");
                usage_and_exit(argv[0]);
            }

            int64_t tmp = -1;
            if (!parse_int64_strict(argv[i + 1], &tmp)) {
                fprintf(stderr, "[ERROR] Invalid --exit-gap-seconds value: %s\n", argv[i + 1]);
                fprintf(stderr, "        Must be a positive integer number of seconds.\n");
                usage_and_exit(argv[0]);
            }
            if (tmp <= 0) {
                fprintf(stderr, "[ERROR] --exit-gap-seconds must be > 0 (got %lld).\n", (long long)tmp);
                usage_and_exit(argv[0]);
            }

            g_exit_gap_seconds = tmp;
            i++;
        } else {
            fprintf(stderr, "[ERROR] Unknown arg: %s\n", argv[i]);
            usage_and_exit(argv[0]);
        }
    }

    printf("[INFO] Starting contact graph build...\n");
    printf("[INFO] exit_token='%s' exit_gap_seconds=%lld\n",
           g_exit_token, (long long)g_exit_gap_seconds);

    parse_input(input);

    FILE *output_fp = fopen(output, "w");
    if (!output_fp) {
        fprintf(stderr, "[FATAL] Could not write to output file: %s\n", output);
        return 1;
    }

    fprintf(output_fp, "NODE_LIST");
    for (int i = 0; i < MAX_PATIENTS; ++i) {
        if (patient_seen[i]) {
            fprintf(output_fp, " %d", i);
        }
    }
    fprintf(output_fp, "\n");

    RoomEntry *entry, *rtmp;
    int room_counter = 0;
    HASH_ITER(hh, room_map, entry, rtmp) {
        printf("[DEBUG] Processing room %s with %d visits\n",
               entry->room_id, entry->visit_count);

        process_room(entry, output_fp);

        HASH_DEL(room_map, entry);
        free(entry->visits); // IMPORTANT: free dynamic visits
        free(entry);
        room_counter++;
    }

    fclose(output_fp);
    printf("[INFO] Done.\n");
    return 0;
}
