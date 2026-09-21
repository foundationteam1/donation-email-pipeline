/**
 * Запускає збір донатів (Zoho → Notion) у GitHub Actions.
 *
 * Навіщо: розклад GitHub (cron) запускає завдання із затримкою від 1 до 4
 * годин. Ця затримка — черга на серверах GitHub, і виправити її неможливо.
 * Але workflow має подію repository_dispatch, яка стартує одразу. Цей скрипт
 * її і викликає.
 *
 * НАЛАШТУВАННЯ (один раз):
 *   1. Створіть у GitHub токен з правом Contents: write на цей репозиторій.
 *   2. Project Settings → Script properties → додайте GITHUB_TOKEN = ваш токен.
 *   3. Project Settings → часовий пояс → (GMT+02:00) Kyiv.
 *
 * ЗАПУСК:
 *   Руками — виберіть функцію triggerFetch угорі та натисніть Run.
 *   За розкладом — Triggers (іконка годинника ліворуч) → Add Trigger →
 *   функція triggerFetch, Time-driven, Day timer, потрібна година.
 */

var REPO = "foundationteam1/donation-email-pipeline";


function triggerFetch() {
  var token = PropertiesService.getScriptProperties().getProperty("GITHUB_TOKEN");
  if (!token) {
    Logger.log("ПОМИЛКА: не знайдено GITHUB_TOKEN у Script Properties. "
               + "Project Settings → Script properties → Add script property.");
    return;
  }

  var response = UrlFetchApp.fetch(
    "https://api.github.com/repos/" + REPO + "/dispatches",
    {
      method: "post",
      headers: {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
      },
      contentType: "application/json",
      // "fetch" мусить збігатися з типом, переліченим у daily.yml
      // у розділі repository_dispatch.
      payload: JSON.stringify({ event_type: "fetch" }),
      muteHttpExceptions: true
    }
  );

  var code = response.getResponseCode();

  // GitHub відповідає 204 без тіла, якщо все гаразд.
  if (code === 204) {
    Logger.log("Запуск збору донатів надіслано в GitHub. "
               + "Перебіг видно у вкладці Actions на GitHub.");
    return;
  }

  if (code === 401 || code === 403) {
    Logger.log("ПОМИЛКА " + code + ": токен не підходить. Найчастіші причини — "
               + "термін дії токена сплив, або йому не дали право Contents: write. "
               + response.getContentText());
    return;
  }

  if (code === 404) {
    Logger.log("ПОМИЛКА 404: репозиторій не знайдено, або токен не має до нього "
               + "доступу. Перевірте REPO у цьому файлі та права токена.");
    return;
  }

  Logger.log("ПОМИЛКА " + code + ": " + response.getContentText());
}
