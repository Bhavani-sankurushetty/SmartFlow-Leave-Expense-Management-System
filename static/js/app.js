/**
 * SmartFlow Enterprise Client JavaScript
 * Supports interactive tab switching, dynamic form updates, and client calculations.
 */

document.addEventListener("DOMContentLoaded", function () {
  // 1. Tab Navigation
  const tabButtons = document.querySelectorAll(".tab-btn");
  tabButtons.forEach((btn) => {
    btn.addEventListener("click", function () {
      const parent = this.closest(".tabs-wrapper") || document;
      const targetId = this.getAttribute("data-tab");

      parent.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      parent.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));

      this.classList.add("active");
      const targetContent = parent.querySelector("#" + targetId);
      if (targetContent) {
        targetContent.classList.add("active");
      }
    });
  });

  // 2. Dynamic Leave Calculator
  const leavePolicySelect = document.getElementById("leave_policy_id");
  const leaveStartDate = document.getElementById("start_date");
  const leaveEndDate = document.getElementById("end_date");
  const leaveDaysDisplay = document.getElementById("calculated_business_days");
  const leaveBalDisplay = document.getElementById("available_balance_display");
  const leaveAlertBox = document.getElementById("leave_calc_alert");

  function calculateLeaveDays() {
    if (!leavePolicySelect || !leaveStartDate || !leaveEndDate) return;
    const policyId = leavePolicySelect.value;
    const start = leaveStartDate.value;
    const end = leaveEndDate.value;

    if (!policyId || !start || !end) {
      if (leaveDaysDisplay) leaveDaysDisplay.textContent = "--";
      if (leaveAlertBox) leaveAlertBox.style.display = "none";
      return;
    }

    fetch(`/api/calculate-leave?policy_id=${encodeURIComponent(policyId)}&start_date=${encodeURIComponent(start)}&end_date=${encodeURIComponent(end)}`)
      .then((res) => res.json())
      .then((data) => {
        if (data.status === "ok") {
          if (leaveDaysDisplay) leaveDaysDisplay.textContent = `${data.business_days} business day(s)`;
          if (leaveBalDisplay) leaveBalDisplay.textContent = `${data.available_balance} day(s)`;
          
          if (leaveAlertBox) {
            leaveAlertBox.style.display = "block";
            if (data.is_eligible) {
              leaveAlertBox.className = "alert alert-success";
              leaveAlertBox.textContent = `Valid date range: ${data.business_days} business day(s) requested. Available balance: ${data.available_balance}.`;
            } else {
              leaveAlertBox.className = "alert alert-warning";
              leaveAlertBox.textContent = `Warning: ${data.reason}`;
            }
          }
        } else {
          if (leaveAlertBox) {
            leaveAlertBox.style.display = "block";
            leaveAlertBox.className = "alert alert-danger";
            leaveAlertBox.textContent = data.error || "Failed to calculate leave days.";
          }
        }
      })
      .catch((err) => {
        console.error("Leave calculation error:", err);
      });
  }

  if (leavePolicySelect) leavePolicySelect.addEventListener("change", calculateLeaveDays);
  if (leaveStartDate) leaveStartDate.addEventListener("change", calculateLeaveDays);
  if (leaveEndDate) leaveEndDate.addEventListener("change", calculateLeaveDays);

  // 3. Dynamic Expense Lines Manager
  const linesTableBody = document.getElementById("expense_lines_tbody");
  const addLineBtn = document.getElementById("add_expense_line_btn");
  const totalAmountDisplay = document.getElementById("claim_total_amount_display");
  const totalAmountHidden = document.getElementById("total_amount_hidden");

  function recalculateExpenseTotal() {
    if (!linesTableBody) return;
    let total = 0;
    const amountInputs = linesTableBody.querySelectorAll(".line-amount-input");
    amountInputs.forEach((input) => {
      const val = parseFloat(input.value);
      if (!isNaN(val) && val > 0) {
        total += val;
      }
    });

    const formatted = total.toFixed(2);
    if (totalAmountDisplay) totalAmountDisplay.textContent = formatted;
    if (totalAmountHidden) totalAmountHidden.value = formatted;
  }

  if (linesTableBody) {
    linesTableBody.addEventListener("input", function (e) {
      if (e.target.classList.contains("line-amount-input")) {
        recalculateExpenseTotal();
      }
    });

    linesTableBody.addEventListener("click", function (e) {
      if (e.target.closest(".remove-line-btn")) {
        const row = e.target.closest("tr");
        if (linesTableBody.querySelectorAll("tr").length > 1) {
          row.remove();
          recalculateExpenseTotal();
        } else {
          alert("An expense claim must have at least one expense line.");
        }
      }
    });
  }

  if (addLineBtn && linesTableBody) {
    addLineBtn.addEventListener("click", function () {
      const rowCount = linesTableBody.querySelectorAll("tr").length + 1;
      const today = new Date().toISOString().split("T")[0];
      const newRow = document.createElement("tr");
      newRow.innerHTML = `
        <td><input type="text" name="line_description[]" class="form-control" placeholder="Expense description" required></td>
        <td>
          <select name="line_category[]" class="form-select" required>
            <option value="Travel">Travel</option>
            <option value="Meals">Meals</option>
            <option value="Accommodation">Accommodation</option>
            <option value="Transportation">Transportation</option>
            <option value="Office Supplies">Office Supplies</option>
            <option value="Software">Software</option>
            <option value="Training">Training</option>
          </select>
        </td>
        <td><input type="date" name="line_date[]" class="form-control" value="${today}" required></td>
        <td><input type="number" step="0.01" min="0.01" name="line_amount[]" class="form-control line-amount-input" placeholder="0.00" required></td>
        <td><input type="checkbox" name="line_receipt_${rowCount}" value="1"></td>
        <td><button type="button" class="btn btn-danger btn-sm remove-line-btn">&times;</button></td>
      `;
      linesTableBody.appendChild(newRow);
    });
  }

  // Initial calculation
  recalculateExpenseTotal();
});
