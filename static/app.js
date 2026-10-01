// Shared confirmation dialog for forms marked with class="confirm-delete".
// Wait until HTML elements exist before finding controls and registering handlers.
document.addEventListener("DOMContentLoaded", function () {
  var modal = document.getElementById("confirm-modal");
  var message = document.getElementById("confirm-modal-message");
  var yesBtn = document.getElementById("confirm-modal-yes");
  var noBtn = document.getElementById("confirm-modal-no");
  // Remember which form should submit if the user chooses Yes.
  var pendingForm = null;

  // Cancel the pending action and hide the dialog.
  function closeModal() {
    modal.hidden = true;
    pendingForm = null;
  }

  document.querySelectorAll("form.confirm-delete").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      // Pause the normal POST until the user confirms.
      event.preventDefault();
      pendingForm = form;
      // data-confirm supplies per-form text; textContent treats it as plain text.
      message.textContent = form.dataset.confirm || "Are you sure?";
      modal.hidden = false;
      yesBtn.focus();
    });
  });

  yesBtn.addEventListener("click", function () {
    var form = pendingForm;
    modal.hidden = true;
    pendingForm = null;
    if (form) {
      // Native submit bypasses this submit listener, avoiding another dialog.
      form.submit();
    }
  });

  noBtn.addEventListener("click", closeModal);

  // A click on the backdrop cancels; clicks inside the dialog do not.
  modal.addEventListener("click", function (event) {
    if (event.target === modal) {
      closeModal();
    }
  });

  // Escape provides a keyboard shortcut for cancelling.
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !modal.hidden) {
      closeModal();
    }
  });
});
