// Mobile drawer navigation toggle for Literature Lab
(function () {
  function initNav() {
    const openBtn = document.getElementById("openRail");
    const closeBtn = document.getElementById("closeRail");
    const backdrop = document.getElementById("railBackdrop");

    function openRail() {
      document.body.classList.add("rail-open");
    }

    function closeRail() {
      document.body.classList.remove("rail-open");
    }

    if (openBtn) {
      openBtn.addEventListener("click", openRail);
    }
    if (closeBtn) {
      closeBtn.addEventListener("click", closeRail);
    }
    if (backdrop) {
      backdrop.addEventListener("click", closeRail);
    }

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && document.body.classList.contains("rail-open")) {
        closeRail();
      }
    });

    // Close drawer when a chat item or navigation link is clicked on mobile/tablet
    const rail = document.getElementById("appRail");
    if (rail) {
      rail.addEventListener("click", (e) => {
        const linkOrChat = e.target.closest(".chat-item, .rail-link, .rail-settings-btn, .settings-nav-item");
        if (linkOrChat && window.innerWidth <= 1024) {
          closeRail();
        }
      });
    }

    window.addEventListener("resize", () => {
      if (window.innerWidth > 1024 && document.body.classList.contains("rail-open")) {
        closeRail();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initNav);
  } else {
    initNav();
  }
})();
