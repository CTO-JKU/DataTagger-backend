document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("textarea.easymde-editor").forEach(function (el) {
        new EasyMDE({element: el, forceSync: true});
    });
});
