// Registers the service worker (homepage.pwa) that makes MakeSetu installable
// and shows the offline page when there's no connection.
(function () {
  if (!("serviceWorker" in navigator)) return;
  var url = document.currentScript && document.currentScript.dataset.sw;
  if (!url) return;
  window.addEventListener("load", function () {
    navigator.serviceWorker.register(url, { scope: "/" }).catch(function () {});
  });
})();
