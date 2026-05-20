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

function repeatedText(amount) {
    return `${amount} repetida${amount === 1 ? "" : "s"}`;
}

function setInlineMessage(card, message, type = "success") {
    const feedback = card.querySelector("[data-inline-feedback]");
    if (!feedback) {
        return;
    }

    feedback.textContent = message || "";
    feedback.classList.remove("success", "error");
    if (message) {
        feedback.classList.add(type === "error" ? "error" : "success");
    }
}

function updateStickerCard(card, data) {
    if (Object.prototype.hasOwnProperty.call(data, "owned")) {
        card.dataset.owned = data.owned ? "1" : "0";
        card.classList.toggle("owned", Boolean(data.owned));
        card.classList.toggle("missing", !data.owned);

        const toggleButton = card.querySelector("[data-toggle-button]");
        if (toggleButton) {
            toggleButton.textContent = data.owned ? "Quitar de mi álbum" : "Ya la tengo";
        }
    }

    if (Object.prototype.hasOwnProperty.call(data, "duplicates")) {
        const amount = Number(data.duplicates) || 0;
        card.dataset.duplicates = String(amount);

        const counter = card.querySelector("[data-repeat-count]");
        if (counter) {
            counter.textContent = repeatedText(amount);
        }
    }
}

function updateProgressStats(progress) {
    if (!progress) {
        return;
    }

    const fields = [
        ["[data-stat-total]", progress.total_stickers],
        ["[data-stat-owned]", progress.owned_count],
        ["[data-stat-duplicates]", progress.duplicate_total],
        ["[data-stat-missing]", progress.missing_count],
    ];

    fields.forEach(([selector, value]) => {
        const element = document.querySelector(selector);
        if (element && value !== undefined) {
            element.textContent = value;
        }
    });
}

document.querySelectorAll("[data-ajax-sticker]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
        event.preventDefault();

        const card = form.closest("[data-sticker-card]");
        const button = form.querySelector("button");
        if (!card || !button) {
            form.submit();
            return;
        }

        const isRepeatAdd = form.dataset.actionType === "repeat-add";
        if (isRepeatAdd && card.dataset.owned !== "1") {
            setInlineMessage(
                card,
                "No puedes marcar esta lámina como repetida porque aún no la tienes en tu álbum.",
                "error"
            );
            return;
        }

        button.disabled = true;
        setInlineMessage(card, "");

        try {
            const response = await fetch(form.action, {
                method: "POST",
                headers: {
                    "Accept": "application/json",
                    "X-Requested-With": "fetch",
                },
            });
            const data = await response.json();

            if (!response.ok || data.ok === false) {
                setInlineMessage(card, data.message || "No se pudo actualizar la lámina.", "error");
                return;
            }

            updateStickerCard(card, data);
            updateProgressStats(data.progress);
            setInlineMessage(card, data.message || "Actualizado.", "success");
        } catch (error) {
            setInlineMessage(card, "No se pudo conectar. Intenta de nuevo.", "error");
        } finally {
            button.disabled = false;
        }
    });
});
