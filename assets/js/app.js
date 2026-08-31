const SUPABASE_URL = "https://txnfdazxrjlldcahsmvu.supabase.co";
const SUPABASE_PUBLISHABLE_KEY = "sb_publishable_tmfeBuePD9ui4DOzvYsVgg_WEnQxi0u";

const supabaseClient = supabase.createClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY);

/*
  DATA SHAPE
  ----------
  fetchDashboardData() below pulls from the views created in schema.sql,
  add_relevance_flag.sql, and add_dashboard_views.sql. It reshapes the
  results into this same object shape the renderers expect:

  {
    opportunities: [{ id, title, persona, mentions, negativePct, trend, quote }],
    competitorGaps: [{ competitor, gap }],
    featureRequests: [{ id, text, count }],
    sentimentByPersona: { "<persona>": { positive, neutral, negative } }
  }
*/

function computeTrend(lastWeek, priorWeek) {
  if (priorWeek === 0) {
    return lastWeek > 0 ? "new" : "flat";
  }
  const pctChange = Math.round(((lastWeek - priorWeek) / priorWeek) * 100);
  if (pctChange === 0) return "flat";
  return pctChange > 0 ? `up ${pctChange}%` : `down ${Math.abs(pctChange)}%`;
}

async function fetchOpportunities(persona, source) {
  const { data: themes, error } = await supabaseClient.rpc("get_theme_stats", {
    persona_filter: persona === "all" ? null : persona,
    source_filter: source === "all" ? null : source
  });

  if (error) {
    console.error("Error fetching opportunities:", error);
    return [];
  }

  const sorted = [...themes].sort((a, b) => b.mentions - a.mentions).slice(0, 6);
  const themeIds = sorted.map((t) => t.theme_id);

  let quoteQuery = supabaseClient
    .from("posts")
    .select("theme_id,content")
    .in("theme_id", themeIds)
    .eq("is_relevant", true)
    .order("posted_at", { ascending: false });

  if (source !== "all") {
    quoteQuery = quoteQuery.eq("source", source);
  }

  const { data: quotePosts } = await quoteQuery;

  const quoteByTheme = {};
  (quotePosts || []).forEach((post) => {
    if (!quoteByTheme[post.theme_id]) {
      quoteByTheme[post.theme_id] = post.content;
    }
  });

  return sorted.map((theme) => ({
    id: theme.theme_id,
    title: theme.title,
    persona: theme.persona,
    mentions: theme.mentions,
    negativePct: theme.negative_pct,
    trend: computeTrend(theme.mentions_last_week, theme.mentions_prior_week),
    quote: quoteByTheme[theme.theme_id] || ""
  }));
}

async function fetchCompetitorGaps(source) {
  const { data, error } = await supabaseClient.rpc("get_competitor_gaps", {
    source_filter: source === "all" ? null : source
  });

  if (error) {
    console.error("Error fetching competitor gaps:", error);
    return [];
  }

  // Pick the top (most-mentioned) theme per competitor.
  const topByCompetitor = {};
  data.forEach((row) => {
    if (!topByCompetitor[row.competitor] || row.mentions > topByCompetitor[row.competitor].mentions) {
      topByCompetitor[row.competitor] = row;
    }
  });

  return Object.entries(topByCompetitor).map(([competitor, row]) => ({
    competitor: competitor.charAt(0).toUpperCase() + competitor.slice(1),
    gap: row.title
  }));
}

async function fetchFeatureRequests(source) {
  const { data, error } = await supabaseClient.rpc("get_feature_requests", {
    source_filter: source === "all" ? null : source
  });

  if (error) {
    console.error("Error fetching feature requests:", error);
    return [];
  }

  const sorted = [...data].sort((a, b) => b.request_count - a.request_count).slice(0, 5);

  return sorted.map((row) => ({
    id: row.theme_id,
    text: row.title,
    count: row.request_count
  }));
}

async function fetchSentimentByPersona(source) {
  const { data, error } = await supabaseClient.rpc("get_persona_sentiment", {
    source_filter: source === "all" ? null : source
  });

  if (error) {
    console.error("Error fetching sentiment by persona:", error);
    return {};
  }

  const result = {};
  data.forEach((row) => {
    result[row.persona] = {
      positive: row.positive_pct || 0,
      neutral: row.neutral_pct || 0,
      negative: row.negative_pct || 0
    };
  });
  return result;
}

async function fetchDashboardData(filters) {
  const [opportunities, competitorGaps, featureRequests, sentimentByPersona] = await Promise.all([
    fetchOpportunities(filters.persona, filters.source),
    fetchCompetitorGaps(filters.source),
    fetchFeatureRequests(filters.source),
    fetchSentimentByPersona(filters.source)
  ]);

  return { opportunities, competitorGaps, featureRequests, sentimentByPersona };
}

async function renderLastUpdated() {
  const el = document.getElementById("last-updated");

  const { data, error } = await supabaseClient
    .from("posts")
    .select("fetched_at")
    .order("fetched_at", { ascending: false })
    .limit(1);

  if (error || !data || data.length === 0) {
    el.textContent = "Last updated: no data yet";
    return;
  }

  const formatted = new Date(data[0].fetched_at).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  });

  el.textContent = `Last updated: ${formatted}`;
}

let sentimentChartInstance = null;

const templates = {};

function captureTemplates() {
  templates.opportunity = document.querySelector("#opportunity-list .opportunity-item").cloneNode(true);
  templates.competitor = document.querySelector("#competitor-gap-list .competitor-card").cloneNode(true);
  templates.featureRequest = document.querySelector("#feature-request-list .feature-request-item").cloneNode(true);
}

function getActiveFilters() {
  return {
    persona: document.getElementById("persona-filter").value,
    source: document.getElementById("source-filter").value,
    timeframe: document.getElementById("timeframe-filter").value
  };
}

function applyFilters(data, filters) {
  // Persona and source filtering both happen server-side now, inside the
  // fetch functions above (via RPC function parameters). Nothing left to
  // filter client-side — this function is now a pass-through, kept so
  // renderDashboard() doesn't need to change.
  return data;
}

function renderOpportunities(opportunities) {
  const list = document.getElementById("opportunity-list");
  list.innerHTML = "";

  if (opportunities.length === 0) {
    list.innerHTML = '<li class="empty-state">No opportunities match these filters yet.</li>';
    return;
  }

  opportunities.forEach((item, index) => {
    const node = templates.opportunity.cloneNode(true);
    node.dataset.opportunityId = item.id;
    node.querySelector(".opportunity-rank").dataset.rank = index + 1;
    node.querySelector(".opportunity-title").textContent = item.title;
    node.querySelector(".meta-persona").textContent = formatPersona(item.persona);
    node.querySelector(".meta-mentions").textContent = `${item.mentions} mentions`;
    node.querySelector(".meta-negative-pct").textContent = `${item.negativePct}% negative`;
    node.querySelector(".meta-trend").textContent = `trending ${item.trend}`;
    node.querySelector(".opportunity-quote").textContent = item.quote;
    list.appendChild(node);
  });
}

function renderCompetitorGaps(competitorGaps) {
  const grid = document.getElementById("competitor-gap-list");
  grid.innerHTML = "";

  if (competitorGaps.length === 0) {
    grid.innerHTML = '<p class="empty-state">No competitor mentions match these filters yet.</p>';
    return;
  }

  competitorGaps.forEach((entry) => {
    const node = templates.competitor.cloneNode(true);
    node.dataset.competitor = entry.competitor;
    node.querySelector(".competitor-name").textContent = entry.competitor;
    node.querySelector(".competitor-gap-text").textContent = entry.gap;
    grid.appendChild(node);
  });
}

function renderFeatureRequests(featureRequests) {
  const list = document.getElementById("feature-request-list");
  list.innerHTML = "";

  if (featureRequests.length === 0) {
    list.innerHTML = '<li class="empty-state">No feature requests match these filters yet.</li>';
    return;
  }

  featureRequests.forEach((item) => {
    const node = templates.featureRequest.cloneNode(true);
    node.dataset.requestId = item.id;
    node.querySelector(".request-text").textContent = item.text;
    node.querySelector(".request-count").textContent = item.count;
    list.appendChild(node);
  });
}

function renderSentimentChart(sentimentByPersona) {
  const labels = Object.keys(sentimentByPersona).map(formatPersona);
  const positive = Object.values(sentimentByPersona).map((v) => v.positive);
  const neutral = Object.values(sentimentByPersona).map((v) => v.neutral);
  const negative = Object.values(sentimentByPersona).map((v) => v.negative);

  const ctx = document.getElementById("sentimentChart");

  if (sentimentChartInstance) {
    sentimentChartInstance.destroy();
  }

  sentimentChartInstance = new Chart(ctx, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [
        { label: "Positive", data: positive, backgroundColor: "#1d8a4a" },
        { label: "Neutral", data: neutral, backgroundColor: "#8a887f" },
        { label: "Negative", data: negative, backgroundColor: "#c4453a" }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: "y",
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (context) => `${context.dataset.label}: ${context.parsed.x}%`
          }
        }
      },
      scales: {
        x: { stacked: true, max: 100, grid: { color: "#e3e1d9" } },
        y: { stacked: true, grid: { display: false } }
      }
    }
  });
}

function formatPersona(persona) {
  const labels = {
    "cofounder-seekers": "Cofounder-seekers",
    "early-talent-job-seekers": "Early-talent job-seekers",
    "non-early-talent-job-seekers": "Non-early-talent job-seekers",
    "employers": "Employers"
  };
  return labels[persona] || persona;
}

async function renderDashboard() {
  const filters = getActiveFilters();
  const rawData = await fetchDashboardData(filters);
  const data = applyFilters(rawData, filters);

  renderOpportunities(data.opportunities);
  renderCompetitorGaps(data.competitorGaps);
  renderFeatureRequests(data.featureRequests);
  renderSentimentChart(data.sentimentByPersona);
}

function init() {
  captureTemplates();
  document.getElementById("persona-filter").addEventListener("change", renderDashboard);
  document.getElementById("source-filter").addEventListener("change", renderDashboard);
  document.getElementById("timeframe-filter").addEventListener("change", renderDashboard);
  renderLastUpdated();
  renderDashboard();
}

document.addEventListener("DOMContentLoaded", init);