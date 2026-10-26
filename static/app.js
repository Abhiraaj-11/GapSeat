const form = document.getElementById("search-form");
const button = document.getElementById("search-button");

if (form && button) {
  form.addEventListener("submit", (event) => {
    if (!form.reportValidity()) {
      event.preventDefault();
      return;
    }

    button.disabled = true;
    button.querySelector("span:first-child").textContent = "Checking chart...";
  });
}