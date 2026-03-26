#!/usr/bin/env bash
# ~/.claude/statusline-command.sh
# Claude Code status line — rich inline formatter with labeled sections

input=$(cat)
j() { printf '%s' "$input" | jq -r "$1"; }

# Core info
model=$(j '.model.display_name // "Claude"')
model_id=$(j '.model.id // ""')
cwd=$(j '.workspace.current_dir // .cwd // ""')
cwd_short=$(basename "$cwd")
version=$(j '.version // ""')
session_id=$(j '.session_id // ""')
session_short="${session_id:0:8}"
exceeds_200k=$(j '.exceeds_200k_tokens // false')

# Context window
ctx_pct=$(j '.context_window.used_percentage // empty')
ctx_remain=$(j '.context_window.remaining_percentage // empty')
ctx_size=$(j '.context_window.context_window_size // 0')
in_tok=$(j '.context_window.total_input_tokens // 0')
out_tok=$(j '.context_window.total_output_tokens // 0')
cur_in=$(j '.context_window.current_usage.input_tokens // empty')
cur_out=$(j '.context_window.current_usage.output_tokens // empty')
cache_create=$(j '.context_window.current_usage.cache_creation_input_tokens // 0')
cache_read=$(j '.context_window.current_usage.cache_read_input_tokens // 0')

# Cost & duration
cost=$(j '.cost.total_cost_usd // 0')
duration_ms=$(j '.cost.total_duration_ms // 0')
api_duration_ms=$(j '.cost.total_api_duration_ms // 0')
lines_add=$(j '.cost.total_lines_added // 0')
lines_rm=$(j '.cost.total_lines_removed // 0')

# Rate limits (Claude.ai subscribers)
rate_5h=$(j '.rate_limits.five_hour.used_percentage // empty')
rate_7d=$(j '.rate_limits.seven_day.used_percentage // empty')

# Extras
vim_mode=$(j '.vim.mode // empty')
agent_name=$(j '.agent.name // empty')
worktree=$(j '.worktree.branch // empty')

# Format tokens as K/M
fmt_k() {
  local n=$1
  if [ "$n" -ge 1000000 ] 2>/dev/null; then
    printf '%.1fM' "$(echo "$n / 1000000" | bc -l)"
  elif [ "$n" -ge 1000 ] 2>/dev/null; then
    printf '%.1fK' "$(echo "$n / 1000" | bc -l)"
  else
    printf '%s' "$n"
  fi
}

# Format duration as human-readable
fmt_dur() {
  local ms=$1
  local s=$((ms / 1000))
  if [ "$s" -ge 3600 ]; then
    printf '%dh%02dm' $((s / 3600)) $(( (s % 3600) / 60 ))
  elif [ "$s" -ge 60 ]; then
    printf '%dm%02ds' $((s / 60)) $((s % 60))
  else
    printf '%ds' "$s"
  fi
}

# Context window size label
ctx_label="ctx"
if [ "$ctx_size" -ge 500000 ] 2>/dev/null; then
  ctx_label="1M"
elif [ "$ctx_size" -ge 100000 ] 2>/dev/null; then
  ctx_label="200K"
fi

# === Build output with unicode separators ===

out=""

# [1] Model + version + mode
out="$model"
if [ -n "$vim_mode" ]; then
  out="$out [$vim_mode]"
fi
if [ -n "$agent_name" ]; then
  out="$out agent:$agent_name"
fi

out="$out | $cwd_short"
if [ -n "$worktree" ]; then
  out="$out @$worktree"
fi

# [2] Context window section
out="$out | $ctx_label"
if [ -n "$ctx_pct" ]; then
  pct_int=$(printf '%.0f' "$ctx_pct")
  # Visual bar: filled/empty blocks
  filled=$((pct_int / 10))
  empty=$((10 - filled))
  bar=""
  for ((i=0; i<filled; i++)); do bar="${bar}#"; done
  for ((i=0; i<empty; i++)); do bar="${bar}-"; done
  out="$out [${bar}] ${pct_int}%"
  if [ "$exceeds_200k" = "true" ]; then
    out="$out !"
  fi
fi

# [3] Tokens: cumulative in/out + cache stats
out="$out | tok: $(fmt_k "$in_tok") in / $(fmt_k "$out_tok") out"

# Cache hit ratio
cache_total=$((cache_create + cache_read))
if [ "$cache_total" -gt 0 ] 2>/dev/null; then
  cache_hit_pct=$(echo "$cache_read * 100 / $cache_total" | bc 2>/dev/null)
  out="$out  cache:${cache_hit_pct:-0}%"
fi

# [4] Cost + time
if [ "$(echo "$cost > 0" | bc -l 2>/dev/null)" = "1" ]; then
  out="$out | \$$(printf '%.2f' "$cost")"
else
  out="$out |"
fi

if [ "$duration_ms" -gt 0 ] 2>/dev/null; then
  out="$out $(fmt_dur "$duration_ms")"
  # API time ratio (how much time spent waiting for API vs wall clock)
  if [ "$api_duration_ms" -gt 0 ] 2>/dev/null; then
    api_pct=$(echo "$api_duration_ms * 100 / $duration_ms" | bc 2>/dev/null)
    out="$out (api:${api_pct}%)"
  fi
fi

# [5] Lines changed
if [ "$lines_add" -gt 0 ] 2>/dev/null || [ "$lines_rm" -gt 0 ] 2>/dev/null; then
  out="$out | +${lines_add} -${lines_rm}"
fi

# [6] Rate limits
if [ -n "$rate_5h" ] || [ -n "$rate_7d" ]; then
  out="$out | rate:"
  if [ -n "$rate_5h" ]; then
    out="$out 5h:$(printf '%.0f' "$rate_5h")%"
  fi
  if [ -n "$rate_7d" ]; then
    out="$out 7d:$(printf '%.0f' "$rate_7d")%"
  fi
fi

printf '%s' "$out"
