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

  var WELCOME_STEPS = [
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

  var PAGE_TOURS = {
    stores: [
      {
        element: "#stores-table",
        popover: {
          title: "Stores",
          description: "Add stores and manage their access and status."
        }
      },
      {
        element: "#setup-section",
        popover: {
          title: "Monthly setup",
          description: "Set monthly targets, budgets and month-end inputs."
        }
      },
      {
        element: "#setup-save",
        popover: {
          title: "Save Setup",
          description: "Save the monthly setup for the selected store and month."
        }
      }
    ],
    daily: [
      {
        element: "#record-day",
        popover: {
          title: "New Entry",
          description: "Record a day's sales, manpower and stock loss."
        }
      },
      {
        element: "#daily-kpis",
        popover: {
          title: "Sales",
          description: "Review sales and other key figures for the month."
        }
      },
      {
        element: "#daily-table",
        popover: {
          title: "Daily Records",
          description: "Review and edit each day's recorded figures."
        }
      }
    ]
  };

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

  function markDone(pageKey) {
    var id = pageKey ? "tour-done-trigger-" + pageKey : "tour-done-trigger";
    var btn = document.getElementById(id);
    if (btn) btn.click();
  }

  function start(pageKey) {
    loadDriver(function (err) {
      if (err) return;
      var mobile = isMobile();
      var sourceSteps = pageKey ? PAGE_TOURS[pageKey] : WELCOME_STEPS;
      if (!sourceSteps) return;
      var steps = sourceSteps.map(function (step) {
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
          markDone(pageKey);
          drv.destroy();
        }
      });
      drv.drive();
    });
  }

  window.ducklabTour = { start: start };
})();
