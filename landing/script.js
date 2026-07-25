const revealItems = document.querySelectorAll(".reveal");

revealItems.forEach((item) => {
  const bounds = item.getBoundingClientRect();
  if (bounds.top < window.innerHeight && bounds.bottom > 0) {
    item.classList.add("is-visible");
  }
});

if ("IntersectionObserver" in window) {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.12 },
  );

  revealItems.forEach((item) => {
    if (!item.classList.contains("is-visible")) observer.observe(item);
  });
} else {
  revealItems.forEach((item) => item.classList.add("is-visible"));
}

const toast = document.querySelector("#apk-toast");
const toastClose = toast?.querySelector("button");
let toastTimer;

function showApkNotice() {
  if (!toast) return;
  window.clearTimeout(toastTimer);
  toast.classList.add("visible");
  toast.setAttribute("aria-hidden", "false");
  toastTimer = window.setTimeout(hideApkNotice, 4200);
}

function hideApkNotice() {
  if (!toast) return;
  toast.classList.remove("visible");
  toast.setAttribute("aria-hidden", "true");
}

document.querySelectorAll("[data-apk-placeholder='true']").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    showApkNotice();
  });
});

toastClose?.addEventListener("click", hideApkNotice);

const year = document.querySelector("#year");
if (year) year.textContent = new Date().getFullYear();
