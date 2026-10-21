/**
 * Тестовий онбординг-лист: надсилає лист на TEST_RECIPIENT, щоб пройти весь шлях
 * лист → кнопка → сторінка опитування (Cloudflare) → Save → відповідь у Notion.
 * Пошта Світлани не задіяна.
 *
 * НАЛАШТУВАННЯ (один раз):
 *   1. Додайте в проєкт HTML-файл onboarding_email з вмістом templates/onboarding_email.html.
 *   2. Project Settings → Script properties → NOTION_TOKEN = токен інтеграції Notion.
 *
 * Запуск: виберіть sendTestOnboarding угорі та натисніть Run.
 * Після тесту видаліть рядок "TEST — …" з бази Onboarding.
 */

var NOTION_VERSION = "2022-06-28";
var ONBOARDING_DATABASE_ID = "ea303c6c00c347fba6792e56bfb32897";
// Сторінка опитування (Cloudflare Worker), та сама, що в GitHub-секреті ONBOARDING_SURVEY_URL.
var SURVEY_URL = "https://kse-survey.foundation-team-e0d.workers.dev/";
// Кому надіслати тестовий лист. Порожньо — на вашу власну пошту.
var TEST_RECIPIENT = "moliinyk@kse.org.ua";

function sendTestOnboarding() {
  var email = TEST_RECIPIENT || Session.getActiveUser().getEmail();

  var response = notionFetch_("https://api.notion.com/v1/pages", "post", {
    parent: { database_id: ONBOARDING_DATABASE_ID },
    properties: {
      "Donor Name": { title: [{ text: { content: "TEST — " + email } }] },
      "Donor Email": { email: email },
      "Sent At": { date: { start: new Date().toISOString() } }
    }
  });
  if (response.getResponseCode() !== 200) {
    throw new Error("Notion " + response.getResponseCode() + ": " + response.getContentText());
  }
  var rowId = JSON.parse(response.getContentText()).id.replace(/-/g, "");

  var html = HtmlService.createHtmlOutputFromFile("onboarding_email").getContent()
    .split("{first_name}").join("Test")
    .split("{survey_url}").join(SURVEY_URL + "?id=" + rowId);

  GmailApp.sendEmail(email, "Welcome to KSE", "This email needs HTML to display.", {
    htmlBody: html,
    name: "KSE Foundation"
  });
  Logger.log("Лист надіслано на " + email + ". Рядок в Onboarding: " + rowId);
}

function notionFetch_(url, method, payload) {
  var token = PropertiesService.getScriptProperties().getProperty("NOTION_TOKEN");
  return UrlFetchApp.fetch(url, {
    method: method,
    headers: { "Authorization": "Bearer " + token, "Notion-Version": NOTION_VERSION },
    contentType: "application/json",
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
}
