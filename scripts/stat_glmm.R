# GLMM delle singole statistiche (glmmTMB), allenati sulle stagioni 2018/19-2024/25,
# previsioni sul 2025/26 per la valutazione (scripts/stat_modelli.py).
#
# Conteggi (tiri da 2, da 3, liberi, rimbalzi off/dif, assist, recuperi, stoppate fatte/subite,
# palle perse): offset log(minuti) + variabili + effetto giocatore + effetto giocatore-stagione.
# Famiglia scelta per AIC tra Poisson, NB1, NB2 sulla stagione 2024/25.
# Percentuali di tiro: binomiale (logit) con gli stessi effetti casuali.
#
# Uscite: data/stat_glmm_pred.parquet, data/stat_glmm_info.csv, data/stat_glmm_coef.csv,
#         log in data/stat_glmm.log
#
#   Rscript stat_glmm.R          (oppure stat_glmm.bat)

suppressPackageStartupMessages({library(arrow); library(glmmTMB); library(data.table)})

args <- commandArgs(trailingOnly = FALSE)
here <- dirname(normalizePath(sub("^--file=", "", args[grep("^--file=", args)])))
root <- dirname(here)
dpath <- function(f) file.path(root, "data", f)
log_file <- dpath("stat_glmm.log")
say <- function(...) {
  msg <- paste0(format(Sys.time(), "%Y-%m-%d %H:%M:%S"), "  ", sprintf(...))
  cat(msg, "\n"); cat(msg, "\n", file = log_file, append = TRUE)
}

THREADS <- max(1, parallel::detectCores() - 1)
TRAIN_SEASONS <- c(1, 3, 5, 7, 9, 13, 19)
SELECT_SEASON <- 19
TEST_SEASON <- 25
COUNTS <- c("fg2a", "tpa", "fta", "oreb", "dreb", "ast", "stl", "blk", "blka", "tov")
PCTS <- list(fg2 = c("fg2m", "fg2a"), tp = c("tpm", "tpa"), ft = c("ftm", "fta"))

d <- as.data.table(read_parquet(dpath("stat_dataset.parquet")))
d[, `:=`(player_id = factor(player_id), season_key = factor(season_key), position = factor(position))]
if (nzchar(Sys.getenv("SMOKE"))) {          # prova veloce: una stagione, una statistica per tipo
  TRAIN_SEASONS <- SELECT_SEASON; COUNTS <- COUNTS[6]; PCTS <- PCTS[3]; log_file <- dpath("stat_glmm_smoke.log")
}
train <- d[season_id %in% TRAIN_SEASONS]
test <- d[season_id == TEST_SEASON]
sfx <- if (nzchar(Sys.getenv("SMOKE"))) "_smoke" else ""
out_pred <- paste0("stat_glmm_pred", sfx, ".parquet"); out_info <- paste0("stat_glmm_info", sfx, ".csv")
out_coef <- paste0("stat_glmm_coef", sfx, ".csv")
say("inizio: train %d righe, test %d righe, %d thread", nrow(train), nrow(test), THREADS)

ctrl <- glmmTMBControl(parallel = THREADS)
fams <- list(poisson = poisson(), nbinom1 = nbinom1(), nbinom2 = nbinom2())

count_formula <- function(s, re = TRUE) {
  f <- sprintf(paste("%s ~ lr_%s + form_%s + loa_%s + dev + pace + home + b2b + miss_usage10 + miss_same30",
                     "+ new_team + z_age + z_height_cm + z_weight_kg + position + offset(log(minutes))"),
               s, s, s, s)
  # sulla sola stagione di scelta giocatore e giocatore-stagione coincidono: solo effetto giocatore
  f <- paste(f, if (re) "+ (1 | player_id) + (1 | season_key)" else "+ (1 | player_id)")
  as.formula(f)
}

pred <- test[, .(game_id, player_id = as.character(player_id))]
info <- list(); coefs <- list()

for (s in COUNTS) {
  t0 <- Sys.time()
  sel <- train[season_id == SELECT_SEASON]
  aics <- sapply(names(fams), function(fn) {
    m <- tryCatch(glmmTMB(count_formula(s, re = FALSE), family = fams[[fn]], data = sel, control = ctrl),
                  error = function(e) NULL)
    a <- if (is.null(m)) NA else AIC(m)
    if (is.na(a)) Inf else a
  })
  best <- names(which.min(aics))
  say("%s: AIC %s -> %s", s, paste(names(aics), round(aics), collapse = " "), best)
  m <- glmmTMB(count_formula(s), family = fams[[best]], data = train, control = ctrl)
  pred[, (paste0("mu_", s)) := predict(m, newdata = test, type = "response", allow.new.levels = TRUE)]
  disp <- if (best == "poisson") NA_real_ else sigma(m)
  vc <- VarCorr(m)$cond
  info[[s]] <- data.table(target = s, family = best, dispersion = disp,
                          sd_player = attr(vc$player_id, "stddev"), sd_player_season = attr(vc$season_key, "stddev"),
                          minutes = round(as.numeric(Sys.time() - t0, units = "mins"), 1))
  cf <- summary(m)$coefficients$cond
  coefs[[s]] <- data.table(target = s, term = rownames(cf), estimate = cf[, 1], se = cf[, 2])
  say("%s: fatto (%s, dispersione %s) in %.1f min", s, best, format(disp, digits = 3),
      as.numeric(Sys.time() - t0, units = "mins"))
  write_parquet(pred, dpath(out_pred))          # salvataggio progressivo
  fwrite(rbindlist(info), dpath(out_info)); fwrite(rbindlist(coefs), dpath(out_coef))
}

for (k in names(PCTS)) {
  t0 <- Sys.time()
  made <- PCTS[[k]][1]; att <- PCTS[[k]][2]
  f <- as.formula(sprintf(paste("cbind(%s, %s - %s) ~ lgt_%s + home + pace + dev + b2b + z_height_cm + position",
                                "+ (1 | player_id) + (1 | season_key)"), made, att, made, k))
  m <- glmmTMB(f, family = binomial(), data = train[get(att) > 0], control = ctrl)
  pred[, (paste0("p_", k)) := predict(m, newdata = test, type = "response", allow.new.levels = TRUE)]
  vc <- VarCorr(m)$cond
  info[[k]] <- data.table(target = k, family = "binomial", dispersion = NA_real_,
                          sd_player = attr(vc$player_id, "stddev"), sd_player_season = attr(vc$season_key, "stddev"),
                          minutes = round(as.numeric(Sys.time() - t0, units = "mins"), 1))
  cf <- summary(m)$coefficients$cond
  coefs[[k]] <- data.table(target = k, term = rownames(cf), estimate = cf[, 1], se = cf[, 2])
  say("%s: fatto in %.1f min", k, as.numeric(Sys.time() - t0, units = "mins"))
  write_parquet(pred, dpath(out_pred))
  fwrite(rbindlist(info), dpath(out_info)); fwrite(rbindlist(coefs), dpath(out_coef))
}
say("FINE")
