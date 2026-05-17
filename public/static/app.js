const menuButton = document.querySelector("[data-menu-button]");
const menu = document.querySelector("[data-menu]");

if (menuButton && menu) {
    menuButton.addEventListener("click", () => {
        menu.classList.toggle("open");
    });
}

document.querySelectorAll("[data-collapsible]").forEach((section) => {
    const label = section.querySelector("[data-toggle-label]");

    function updateLabel() {
        if (label) {
            label.textContent = section.open ? "Ocultar" : "Ver más";
        }
    }

    updateLabel();
    section.addEventListener("toggle", updateLabel);
});
