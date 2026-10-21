/**
 * Сторінка опитування з онбординг-листа і запис відповідей у Notion.
 * Cloudflare Worker: відкривається будь-кому без входу в акаунт.
 *
 *   GET  /?id=<ID рядка Onboarding>&choice=events   → сторінка з галочками
 *   POST /save  {id, choices, lang}                  → запис у Notion
 *
 * Сам клік у листі нічого не записує: поштові антивіруси відкривають усі
 * посилання в листах, і без кнопки Save в базу потрапляли б фальшиві відповіді.
 *
 * НАЛАШТУВАННЯ (один раз):
 *   1. Workers & Pages → Create application → Create Worker → Deploy.
 *   2. Edit code → вставити цей файл → Deploy.
 *   3. Settings → Variables and Secrets → Add → Type: Secret,
 *      Name: NOTION_TOKEN, Value: токен інтеграції Notion.
 *   4. Адресу воркера (https://….workers.dev) додати в GitHub-секрет ONBOARDING_SURVEY_URL.
 */

const NOTION_VERSION = "2022-06-28";
// Worker only ever reads and writes rows of this database.
const ONBOARDING_DATABASE_ID = "ea303c6c00c347fba6792e56bfb32897";

// Значення з посилань у листі → назви варіантів у колонках Notion.
const INTERESTS = {
  news: "KSE news and updates",
  reports: "Impact and annual reports",
  events: "Events and invitations",
  support: "Opportunities to support",
};
const LANGUAGES = { uk: "Українська", en: "English" };

const has = (map, key) => typeof key === "string" && Object.prototype.hasOwnProperty.call(map, key);

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (request.method === "GET" && url.pathname === "/") return await showSurvey(url, env);
      if (request.method === "POST" && url.pathname === "/save") return await saveAnswers(request, env);
      return new Response("Not found", { status: 404 });
    } catch (err) {
      console.log("Error:", err.stack || err);
      return new Response("Something went wrong", { status: 500 });
    }
  },
};

async function showSurvey(url, env) {
  const id = (url.searchParams.get("id") || "").trim();
  const page = await getOnboardingRow(id, env);
  if (!page) return html(`<div class="card"><p>This link is not valid.</p></div>`, 404);

  // Відмічено лише те, що донор натиснув у листі.
  const choice = url.searchParams.get("choice");
  const picked = has(INTERESTS, choice) ? [choice] : [];
  const langParam = url.searchParams.get("lang");
  const lang = has(LANGUAGES, langParam) ? langParam : "";

  const checkbox = (k) =>
    `<label><input type="checkbox" name="choice" value="${k}"${picked.includes(k) ? " checked" : ""}> ${INTERESTS[k]}</label>`;
  const radio = (k) =>
    `<label><input type="radio" name="lang" value="${k}"${lang === k ? " checked" : ""}> ${LANGUAGES[k]}</label>`;

  return html(`
<div class="card">
  <form id="f">
    <h1>Thank you for being with KSE</h1>
    <p class="muted">Choose what you would like to receive, then press Save.</p>
    <fieldset><legend>What would you like to receive?</legend>
      ${Object.keys(INTERESTS).map(checkbox).join("\n      ")}
    </fieldset>
    <fieldset><legend>Preferred language</legend>
      ${Object.keys(LANGUAGES).map(radio).join("\n      ")}
    </fieldset>
    <button type="submit" id="save">Save</button>
    <p class="error" id="error" hidden>Something went wrong. Please try again.</p>
  </form>
  <p class="thanks" id="thanks" hidden>Thank you! Your preferences have been saved.</p>
</div>
<script>
  var ID = ${JSON.stringify(id.replace(/[^0-9a-f-]/gi, ""))};
  document.getElementById("f").addEventListener("submit", function (e) {
    e.preventDefault();
    var choices = [].map.call(document.querySelectorAll("input[name=choice]:checked"), function (i) { return i.value; });
    var lang = document.querySelector("input[name=lang]:checked");
    var btn = document.getElementById("save");
    btn.disabled = true;
    document.getElementById("error").hidden = true;
    fetch("/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: ID, choices: choices, lang: lang ? lang.value : "" })
    }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      document.getElementById("f").hidden = true;
      document.getElementById("thanks").hidden = false;
    }).catch(function () {
      btn.disabled = false;
      document.getElementById("error").hidden = false;
    });
  });
</script>`);
}

async function saveAnswers(request, env) {
  // Лише JSON: звичайна HTML-форма з чужого сайту так надіслати не може.
  if (!(request.headers.get("Content-Type") || "").startsWith("application/json")) {
    return new Response("Bad request", { status: 400 });
  }
  let body;
  try { body = await request.json(); } catch { return new Response("Bad request", { status: 400 }); }
  const { id, lang } = body || {};
  const choices = Array.isArray(body && body.choices) ? body.choices : [];
  if (!(await getOnboardingRow(id, env))) return new Response("Invalid link", { status: 400 });

  const properties = {
    "Interests": { multi_select: choices.filter((c) => has(INTERESTS, c)).map((c) => ({ name: INTERESTS[c] })) },
    "Language": { select: has(LANGUAGES, lang) ? { name: LANGUAGES[lang] } : null },
    "Answered At": { date: { start: new Date().toISOString() } },
  };
  const response = await notion(`pages/${id}`, env, "PATCH", { properties });
  if (!response.ok) {
    console.log("Notion error", response.status, await response.text());
    return new Response("Could not save", { status: 502 });
  }
  return new Response("OK");
}

/** Повертає рядок бази Onboarding або null, якщо ID невірний. */
async function getOnboardingRow(id, env) {
  if (typeof id !== "string" || !/^[0-9a-f-]{32,36}$/i.test(id)) return null;
  const response = await notion(`pages/${id}`, env, "GET");
  if (!response.ok) {
    console.log("Notion GET page failed", response.status, await response.text());
    return null;
  }
  const page = await response.json();
  // Приймаємо лише рядки бази Onboarding, а не будь-яку сторінку, до якої має доступ токен.
  const parent = ((page.parent && page.parent.database_id) || "").replace(/-/g, "");
  if (parent !== ONBOARDING_DATABASE_ID || page.archived) {
    console.log("Page is not an Onboarding row", JSON.stringify(page.parent), "archived:", page.archived);
    return null;
  }
  return page;
}

function notion(path, env, method, payload) {
  return fetch(`https://api.notion.com/v1/${path}`, {
    method,
    headers: {
      "Authorization": `Bearer ${env.NOTION_TOKEN}`,
      "Notion-Version": NOTION_VERSION,
      "Content-Type": "application/json",
    },
    body: payload ? JSON.stringify(payload) : undefined,
  });
}

function html(content, status = 200) {
  return new Response(`<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>KSE — Your preferences</title>
<style>
  body{margin:0;background:#f2f4f7;font-family:Arial,Helvetica,sans-serif;color:#17202b;line-height:1.5}
  .card{max-width:520px;margin:40px auto;background:#fff;border-radius:8px;padding:28px 32px}
  h1{font-size:22px;margin:0 0 6px}
  .muted{color:#5d6b7a;margin:0 0 18px}
  fieldset{border:1px solid #d6dce4;border-radius:8px;padding:12px 16px;margin:0 0 16px}
  legend{font-weight:bold;padding:0 4px}
  label{display:flex;gap:10px;align-items:center;padding:6px 0;cursor:pointer}
  input{width:18px;height:18px;accent-color:#1f4fd1}
  button{background:#1f4fd1;color:#fff;border:0;border-radius:6px;padding:12px 22px;font-size:15px;font-weight:bold;cursor:pointer}
  button:disabled{opacity:.6}
  .thanks{background:#ddf2e6;color:#1d7a4a;padding:14px;border-radius:6px;font-weight:bold}
  .error{color:#b42318;margin:10px 0 0}
  @media (max-width:560px){.card{margin:16px;padding:22px 18px}}
</style>
</head>
<body>${content}</body>
</html>`, {
    status,
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      // Не кешувати, не вбудовувати в чужі сайти, не передавати адресу з ID іншим сайтам.
      "Cache-Control": "no-store",
      "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; form-action 'none'; base-uri 'none'",
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
