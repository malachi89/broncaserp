(function () {
  function isStepOneInput(input) {
    return input instanceof HTMLInputElement && input.type === "number" && input.dataset.stepOne === "true";
  }

  function toNumber(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  document.addEventListener("keydown", function (event) {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    if (!isStepOneInput(event.target)) return;

    event.preventDefault();
    const input = event.target;
    const current = toNumber(input.value);
    const next = event.key === "ArrowUp" ? current + 1 : current - 1;

    input.value = String(next);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
})();
