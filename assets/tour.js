/* Opscore guided tour — driver.js 1.3.1 (bundled locally, no CDN).
   Loads driver.js dynamically from /driver.js (static asset), builds the
   step list, and starts the tour. On narrow screens (<=480px) steps render
   as centred dialogs without element highlighting.

   Completion (finish or skip) is reported to the backend by clicking the
   hidden button rendered in the app shell (id="tour-done-trigger"), which
   is bound to the OpsState.tour_finished event handler. That keeps the
   whole flow inside Reflex's compiled event machinery — no window-global
   event wiring.
*/
(function () {
  "use strict";

  var DRIVER_SRC = "/driver.js";
  var MOBILE_BREAKPOINT = 480; // px; phones (e.g. 390px) use dialog mode

  var STEPS = [
    {
      element: null, // centred welcome
      popover: {
        title: "Welcome to Opscore",
        description: "Your store's sales, expenses, manpower and stock in one place."
      }
    },
    {
      element: "#nav-ops-stores",
      popover: {
        title: "Store Management",
        description: "Start here. Add your store and set its monthly targets."
      }
    },
    {
      element: "#nav-ops-daily",
      popover: {
        title: "Daily Operations",
        description: "Key in each day's sales, manpower and stock loss here."
      }
    },
    {
      element: "#nav-ops",
      popover: {
        title: "Dashboard",
        description: "Your month at a glance."
      }
    },
    {
      element: "#nav-ops-sales",
      popover: {
        title: "Sales Performance",
        description: "Targets, daily progress and basket."
      }
    },
    {
      element: "#nav-ops-expenses",
      popover: {
        title: "Expenses Management",
        description: "Budgets and paid expenses."
      }
    },
    {
      element: "#nav-ops-manpower",
      popover: {
        title: "Manpower Efficiency",
        description: "Hours, payroll and productivity."
      }
    },
    {
      element: "#nav-ops-stock",
      popover: {
        title: "Stock Management",
        description: "Stock loss by category."
      }
    },
    {
      element: "#nav-ops-report",
      popover: {
        title: "Monthly Report",
        description: "The full month, ready to share."
      }
    },
    {
      element: "#nav-ops-actions",
      popover: {
        title: "Action Tracker",
        description: "Follow up issues until they're closed."
      }
    },
    {
      element: "#nav-ops-settings",
      popover: {
        title: "Settings",
        description: "Password, reference data and backups."
      }
    },
    {
      element: null, // centred farewell
      popover: {
        title: "All Set",
        description: "Replay this tour anytime from Settings."
      }
    }
  ];

  function isMobile() {
    return window.innerWidth <= MOBILE_BREAKPOINT;
  }

  function loadDriver(cb) {
    if (window.driver && window.driver.js && window.driver.js.driver) {
      cb(null);
      return;
    }
    var s = document.createElement("script");
    s.src = DRIVER_SRC;
    s.onload = function () { cb(null); };
    s.onerror = function () { cb(new Error("driver.js failed to load")); };
    document.head.appendChild(s);
  }

  function markDone() {
    var btn = document.getElementById("tour-done-trigger");
    if (btn) btn.click();
  }

  function start() {
    loadDriver(function (err) {
      if (err) return;
      var mobile = isMobile();
      var steps = STEPS.map(function (step) {
        if (mobile) {
          // Phones: centred dialog, no highlight.
          return { popover: step.popover };
        }
        return step;
      });
      var drv = window.driver.js.driver({
        showProgress: true,
        overlayOpacity: 0.7,
        stagePadding: 6,
        allowClose: true,
        nextBtnText: "Next",
        prevBtnText: "Back",
        doneBtnText: "Done",
        progressText: "Step {{current}} of {{total}}",
        popoverClass: mobile ? "ducklab-tour-mobile" : "",
        steps: steps,
        onDestroyStarted: function () {
          // Finish or Skip/close: mark the tour done, then tear down.
          markDone();
          drv.destroy();
        }
      });
      drv.drive();
    });
  }

  window.ducklabTour = { start: start };
})();
