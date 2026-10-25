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
    dashboard: [
      {
        element: "#pillar-strip",
        popover: {
          title: "Monthly Overview",
          description: "See the month's main sales, expenses, salary and stock figures."
        }
      },
      {
        element: "#overview-pace",
        popover: {
          title: "Sales Trend",
          description: "Track daily sales and progress toward the target."
        }
      },
      {
        element: "#targets-panel",
        popover: {
          title: "Targets",
          description: "Compare sales with each target level."
        }
      },
      {
        element: "#attention-panel",
        popover: {
          title: "Alerts",
          description: "Find items that need attention."
        }
      },
      {
        element: "#at-a-glance",
        popover: {
          title: "Summary",
          description: "Review additional monthly figures in one place."
        }
      }
    ],
    report: [
      {
        element: "#sales-section",
        popover: {
          title: "Sales",
          description: "Review sales results, targets, achievement and basket metrics."
        }
      },
      {
        element: "#expenses-section",
        popover: {
          title: "Expenses",
          description: "Review monthly expenses, budgets and category totals."
        }
      },
      {
        element: "#manpower-section",
        popover: {
          title: "Manpower",
          description: "Review headcount, payroll ratios, turnover and leave."
        }
      },
      {
        element: "#stock-section",
        popover: {
          title: "Stock",
          description: "Review stock loss totals, ratios and category treatments."
        }
      },
      {
        element: "#actions-section",
        popover: {
          title: "Actions",
          description: "Review action totals by status."
        }
      }
    ],
    sales: [
      {
        element: "#sales-kpis",
        popover: {
          title: "Sales Summary",
          description: "See sales, achievement, target level and daily averages."
        }
      },
      {
        element: "#basket-section",
        popover: {
          title: "Basket",
          description: "Compare basket metrics with state averages."
        }
      },
      {
        element: "#pace-section",
        popover: {
          title: "Sales Trend",
          description: "Track daily sales and target progress through the month."
        }
      },
      {
        element: "#ladder-section",
        popover: {
          title: "Targets",
          description: "Compare achievement and gaps across target levels."
        }
      },
      {
        element: "#sales-daily-section",
        popover: {
          title: "Daily Sales",
          description: "Review daily sales, targets, transactions and results."
        }
      }
    ],
    expenses: [
      {
        element: "#expense-kpis",
        popover: {
          title: "Expense Summary",
          description: "See paid, incurred, pending and budget figures."
        }
      },
      {
        element: "#budget-monitor",
        popover: {
          title: "Budget vs Paid",
          description: "Compare category budgets with paid expenses and set budgets."
        }
      },
      {
        element: "#expense-register",
        popover: {
          title: "Transactions",
          description: "Filter, review and edit expense transactions."
        }
      }
    ],
    manpower: [
      {
        element: "#headcount-section",
        popover: {
          title: "Headcount",
          description: "Review workforce changes, budget variance and turnover."
        }
      },
      {
        element: "#efficiency-section",
        popover: {
          title: "Productivity",
          description: "Review sales per effective staff member."
        }
      },
      {
        element: "#payroll-section",
        popover: {
          title: "Salary",
          description: "Review payroll costs and salary ratios against targets."
        }
      },
      {
        element: "#leave-section",
        popover: {
          title: "Leave",
          description: "Review leave totals by type."
        }
      },
      {
        element: "#staffing-table",
        popover: {
          title: "Daily Staffing",
          description: "Review and edit daily staffing and productivity records."
        }
      }
    ],
    stock: [
      {
        element: "#stock-kpi-section",
        popover: {
          title: "Stock Summary",
          description: "See total loss, loss ratio, missing stock and extra stock."
        }
      },
      {
        element: "#trend-section",
        popover: {
          title: "Daily Loss",
          description: "Track stock loss across reported days."
        }
      },
      {
        element: "#category-section",
        popover: {
          title: "Categories",
          description: "Review loss by category and inspect individual entries."
        }
      },
      {
        element: "#daily-section",
        popover: {
          title: "Daily Stock Loss",
          description: "Review daily stock loss figures and statuses."
        }
      },
      {
        element: "#comparison-section",
        popover: {
          title: "Comparison",
          description: "Compare this month's stock figures with the previous month."
        }
      }
    ],
    actions: [
      {
        element: "#action-cards",
        popover: {
          title: "Action Summary",
          description: "See open, overdue and completed action totals."
        }
      },
      {
        element: "#action-filters",
        popover: {
          title: "Action Filters",
          description: "Narrow actions by status, pillar, priority and overdue state."
        }
      },
      {
        element: "#actions-table",
        popover: {
          title: "Actions",
          description: "Filter, review and edit actions and their follow-up details."
        }
      }
    ],
    settings: [
      {
        element: "#settings-account",
        popover: {
          title: "Account",
          description: "Review your account details and security settings."
        }
      },
      {
        element: "#pw-section",
        popover: {
          title: "Change Password",
          description: "Update your password and sign out other sessions."
        }
      },
      {
        element: "#tour-section",
        popover: {
          title: "Guided Tour",
          description: "Replay the welcome tour from the Dashboard."
        }
      }
    ],
    stores: [
      {
        element: "#stores-table",
        popover: {
          title: "Stores",
          description: "Add your store and keep its details up to date."
        }
      },
      {
        element: "#setup-section",
        popover: {
          title: "Monthly Setup",
          description: "Set the month's targets, levels, budgets and month-end inputs."
        }
      },
      {
        element: "#setup-holidays-field",
        popover: {
          title: "Public Holidays",
          description: "Mark holidays so daily targets use the right share."
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
          title: "Monthly Summary",
          description: "See sales, achievement, transactions, units, loss and expenses for the month."
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
