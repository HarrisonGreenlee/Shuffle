from rules_registry import register_rule

# Example of an external rule.

# First, define a hash function. All datetimes that emit the same hash function will be considered interchangable.
def same_hour(dt):
    # dt is a datetime; return an int 0..23
    return dt.hour

# Next, register the function with a name and description so it can appear in the GUI.
register_rule(
    "Shuffle by Hour",
    same_hour,
    "Only shuffle among arrivals that occurred in the same hour-of-day (0..23)."
)

# Add more rules here: