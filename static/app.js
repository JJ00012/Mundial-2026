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

function downloadBlob(blob, filename) {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
}

function canvasToBlob(canvas) {
    return new Promise((resolve) => {
        canvas.toBlob((blob) => resolve(blob), "image/png", 0.98);
    });
}

function loadHtml2Canvas() {
    if (typeof html2canvas !== "undefined") {
        return Promise.resolve(html2canvas);
    }

    return new Promise((resolve, reject) => {
        const existingScript = document.querySelector("[data-html2canvas-script]");
        if (existingScript) {
            existingScript.addEventListener("load", () => resolve(html2canvas), { once: true });
            existingScript.addEventListener("error", reject, { once: true });
            return;
        }

        const script = document.createElement("script");
        script.src = "https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js";
        script.async = true;
        script.dataset.html2canvasScript = "true";
        script.addEventListener("load", () => resolve(html2canvas), { once: true });
        script.addEventListener("error", reject, { once: true });
        document.head.appendChild(script);
    });
}

async function shareRepeatsCard(button) {
    const card = document.querySelector("[data-share-card]");
    if (!card) {
        button.textContent = "No se pudo generar la imagen";
        setTimeout(() => {
            button.textContent = "Compartir repetidas";
        }, 1800);
        return;
    }

    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "Generando imagen...";

    try {
        const html2canvasLibrary = await loadHtml2Canvas();
        const canvas = await html2canvasLibrary(card, {
            backgroundColor: "#f7fbf7",
            scale: Math.min(window.devicePixelRatio || 2, 3),
            useCORS: true,
        });
        const blob = await canvasToBlob(canvas);
        const file = new File([blob], "repetidas-mundial-2026.png", { type: "image/png" });

        if (navigator.canShare && navigator.canShare({ files: [file] })) {
            await navigator.share({
                files: [file],
                title: "Mis repetidas Mundial 2026",
                text: "Estas son mis láminas repetidas para intercambiar.",
            });
        } else {
            downloadBlob(blob, "repetidas-mundial-2026.png");
        }
    } catch (error) {
        button.textContent = "Descarga no disponible";
        setTimeout(() => {
            button.textContent = originalText;
            button.disabled = false;
        }, 1800);
        return;
    }

    button.disabled = false;
    button.textContent = originalText;
}

const shareButton = document.querySelector("[data-share-repeats]");

if (shareButton) {
    shareButton.addEventListener("click", () => shareRepeatsCard(shareButton));
}
