data_file <- file.path("data", "energy_usage.csv")
output_dir <- "analysis"

if (!file.exists(data_file)) {
  stop("Energy data file not found. Run this script from the project folder.")
}

energy <- read.csv(data_file, stringsAsFactors = FALSE)
required_columns <- c("date", "usage_kwh", "cost_inr", "solar_kwh", "carbon_kg")
missing_columns <- setdiff(required_columns, names(energy))

if (length(missing_columns) > 0) {
  stop(sprintf("Missing required columns: %s", paste(missing_columns, collapse = ", ")))
}

energy$date <- as.Date(energy$date)
if (anyNA(energy$date) || any(!is.finite(as.matrix(energy[required_columns[-1]])))) {
  stop("The energy data contains invalid dates or non-numeric measurements.")
}

energy <- energy[order(energy$date), ]
energy$rolling_7_day_kwh <- stats::filter(
  energy$usage_kwh,
  rep(1 / 7, 7),
  sides = 1
)
energy$week_start <- energy$date - (as.integer(format(energy$date, "%u")) - 1)

weekly <- aggregate(
  cbind(usage_kwh, cost_inr, solar_kwh, carbon_kg) ~ week_start,
  data = energy,
  FUN = sum
)
weekly$solar_share_percent <- ifelse(
  weekly$usage_kwh > 0,
  round(100 * weekly$solar_kwh / weekly$usage_kwh, 1),
  0
)

average_daily_usage <- mean(energy$usage_kwh)
highest_day <- energy[which.max(energy$usage_kwh), ]
latest_week <- tail(weekly, 1)

cat("Smart Energy Consumption Summary\n")
cat("--------------------------------\n")
cat(sprintf("Days analyzed: %d\n", nrow(energy)))
cat(sprintf("Average daily usage: %.1f kWh\n", average_daily_usage))
cat(sprintf(
  "Highest-use day: %s (%.1f kWh)\n",
  format(highest_day$date, "%Y-%m-%d"),
  highest_day$usage_kwh
))
cat(sprintf(
  "Latest week: %.1f kWh, INR %.0f cost, %.1f%% solar share\n",
  latest_week$usage_kwh,
  latest_week$cost_inr,
  latest_week$solar_share_percent
))

if (!dir.exists(output_dir)) {
  dir.create(output_dir, recursive = TRUE)
}

write.csv(energy, file.path(output_dir, "daily_analysis.csv"), row.names = FALSE)
write.csv(weekly, file.path(output_dir, "weekly_summary.csv"), row.names = FALSE)
cat(sprintf("Analysis files saved to %s/.\n", output_dir))
