(function () {
  const productButtons = Array.from(document.querySelectorAll(".product-tile"));
  const searchInput = document.getElementById("barcode-input");
  const cartLines = document.getElementById("cart-lines");
  const cartTotal = document.getElementById("cart-total");
  const cartJson = document.getElementById("cart-json");
  const posForm = document.getElementById("pos-form");
  const clearCart = document.getElementById("clear-cart");
  const paymentMethod = document.getElementById("payment-method");
  const customerSelect = document.getElementById("customer-id");
  const posFeedback = document.getElementById("pos-feedback");
  const cashTenderSection = document.getElementById("cash-tender-section");
  const tenderedAmountInput = document.getElementById("tendered-amount");
  const resetTenderedButton = document.getElementById("reset-tendered");
  const billButtons = Array.from(document.querySelectorAll(".bill-button"));
  const tenderedTotal = document.getElementById("tendered-total");
  const changeLabel = document.getElementById("change-label");
  const changeTotal = document.getElementById("change-total");
  const submitButton = posForm ? posForm.querySelector("button[type='submit']") : null;
  const isSaleSaved = posForm && posForm.dataset.saleSaved === "1";
  const hasOpenCashSession = posForm && posForm.dataset.cashSession === "1";
  const POS_DRAFT_KEY = "broncaserp:pos:draft:v1";
  const cart = new Map();
  let cartGrandTotal = 0;
  let isSubmittingSale = false;

  function asMoneyCents(value) {
    return Math.round(Number(value || 0) * 100);
  }

  function normalize(value) {
    return String(value || "").trim().toLowerCase();
  }

  function productFromButton(button) {
    return {
      id: Number(button.dataset.productId),
      name: button.dataset.name,
      barcode: button.dataset.barcode,
      sku: button.dataset.sku,
      price: Number(button.dataset.price),
    };
  }

  function money(value) {
    return new Intl.NumberFormat("es-MX", {
      style: "currency",
      currency: "MXN",
    }).format(value);
  }

  function parseAmount(value) {
    const amount = Number(value);
    if (!Number.isFinite(amount) || amount < 0) return 0;
    return amount;
  }

  function readTenderedAmount() {
    return parseAmount(tenderedAmountInput ? tenderedAmountInput.value : 0);
  }

  function setTenderedAmount(amount) {
    if (!tenderedAmountInput) return;
    tenderedAmountInput.value = parseAmount(amount).toFixed(2);
  }

  function clearPersistedDraft() {
    try {
      window.sessionStorage.removeItem(POS_DRAFT_KEY);
    } catch (_error) {
      // Ignora errores de storage del navegador.
    }
  }

  function saveDraft() {
    try {
      const payload = {
        cart: Array.from(cart.values()).map((lineState) => ({
          product: lineState.product,
          quantity: Number(lineState.quantity),
          unitPrice: Number(lineState.unitPrice),
          priceOverrideReason: lineState.priceOverrideReason || "",
        })),
        paymentMethod: paymentMethod ? paymentMethod.value : "cash",
        customerId: customerSelect ? customerSelect.value : "",
        tenderedAmount: readTenderedAmount(),
      };
      window.sessionStorage.setItem(POS_DRAFT_KEY, JSON.stringify(payload));
    } catch (_error) {
      // Si storage está bloqueado, POS sigue funcionando sin persistencia.
    }
  }

  function restoreDraft() {
    try {
      const raw = window.sessionStorage.getItem(POS_DRAFT_KEY);
      if (!raw) return;
      const draft = JSON.parse(raw);
      const draftLines = Array.isArray(draft.cart) ? draft.cart : [];
      draftLines.forEach((line) => {
        const product = line.product || {};
        const productId = Number(product.id);
        const quantity = Number(line.quantity);
        const unitPrice = Number(line.unitPrice);
        if (!Number.isFinite(productId) || productId <= 0) return;
        if (!Number.isFinite(quantity) || quantity <= 0) return;
        if (!Number.isFinite(unitPrice) || unitPrice <= 0) return;
        cart.set(productId, {
          product: {
            id: productId,
            name: String(product.name || "Producto"),
            barcode: String(product.barcode || ""),
            sku: String(product.sku || ""),
            price: Number(product.price || unitPrice),
          },
          quantity,
          unitPrice,
          priceOverrideReason: String(line.priceOverrideReason || ""),
        });
      });

      if (paymentMethod && draft.paymentMethod) {
        const existsOption = Array.from(paymentMethod.options).some((option) => option.value === draft.paymentMethod);
        if (existsOption) {
          paymentMethod.value = draft.paymentMethod;
        }
      }
      if (tenderedAmountInput && draft.tenderedAmount !== undefined) {
        setTenderedAmount(draft.tenderedAmount);
      }
      if (customerSelect && draft.customerId !== undefined) {
        customerSelect.value = String(draft.customerId || "");
      }
    } catch (_error) {
      clearPersistedDraft();
    }
  }

  function showFeedback(message, level) {
    if (!posFeedback) return;
    posFeedback.textContent = message;
    posFeedback.classList.remove("is-hidden", "error", "warn");
    posFeedback.classList.add(level || "warn");
  }

  function clearFeedback() {
    if (!posFeedback) return;
    posFeedback.textContent = "";
    posFeedback.classList.add("is-hidden");
    posFeedback.classList.remove("error", "warn");
  }

  function syncStockFeedback() {
    // El POS permite vender con/ sin existencia; no mostrar advertencias de stock.
  }

  function syncCustomerRequirement() {
    if (!paymentMethod || !customerSelect) return;
    customerSelect.required = paymentMethod.value === "credit";
  }

  function updateCashTenderUI() {
    if (!cashTenderSection || !paymentMethod || !tenderedTotal || !changeTotal || !changeLabel) return;

    const isCash = paymentMethod.value === "cash";
    cashTenderSection.classList.toggle("is-hidden", !isCash);
    if (!isCash) return;

    const tendered = readTenderedAmount();
    const balance = tendered - cartGrandTotal;

    tenderedTotal.textContent = money(tendered);
    if (balance >= 0) {
      changeLabel.textContent = "Cambio";
      changeTotal.textContent = money(balance);
      changeTotal.classList.add("positive");
      changeTotal.classList.remove("negative");
      return;
    }

    changeLabel.textContent = "Faltan";
    changeTotal.textContent = money(Math.abs(balance));
    changeTotal.classList.add("negative");
    changeTotal.classList.remove("positive");
  }

  function addProduct(product) {
    const current = cart.get(product.id) || { product, quantity: 0, unitPrice: product.price, priceOverrideReason: "" };
    current.quantity += 1;
    cart.set(product.id, current);
    renderCart();
  }

  function findProduct(query) {
    const clean = normalize(query);
    if (!clean) return null;
    const exact = productButtons.find((button) => {
      const product = productFromButton(button);
      return normalize(product.barcode) === clean || normalize(product.sku) === clean;
    });
    if (exact) return productFromButton(exact);
    const partial = productButtons.find((button) => normalize(button.dataset.name).includes(clean));
    return partial ? productFromButton(partial) : null;
  }

  function renderCart() {
    cartLines.innerHTML = "";
    let total = 0;
    cart.forEach((lineState) => {
      const { product, quantity } = lineState;
      if (!Number.isFinite(lineState.unitPrice) || lineState.unitPrice <= 0) {
        lineState.unitPrice = product.price;
      }
      const isPriceOverride = asMoneyCents(lineState.unitPrice) !== asMoneyCents(product.price);
      const lineTotal = lineState.unitPrice * quantity;
      total += lineTotal;
      const line = document.createElement("div");
      line.className = "cart-line";
      line.innerHTML = `
        <div>
          <strong title="${product.name}">${product.name}</strong>
        </div>
        <input type="number" min="0.001" step="any" data-step-one="true" value="${quantity}" aria-label="Cantidad de ${product.name}">
        <input type="number" min="0.01" step="0.01" value="${lineState.unitPrice.toFixed(2)}" aria-label="Precio unitario de ${product.name}">
        <span>${money(lineTotal)}</span>
        <button type="button" title="Quitar">x</button>
      `;
      const [quantityInput, unitPriceInput] = line.querySelectorAll("input");
      quantityInput.addEventListener("change", () => {
        const nextQuantity = Number(quantityInput.value);
        if (!nextQuantity || nextQuantity <= 0) {
          cart.delete(product.id);
        } else {
          lineState.quantity = nextQuantity;
          cart.set(product.id, lineState);
        }
        renderCart();
      });
      unitPriceInput.addEventListener("change", () => {
        const nextUnitPrice = Number(unitPriceInput.value);
        if (!nextUnitPrice || nextUnitPrice <= 0) {
          showFeedback(`El precio de ${product.name} debe ser mayor a cero.`, "error");
          lineState.unitPrice = product.price;
        } else {
          lineState.unitPrice = nextUnitPrice;
        }
        if (asMoneyCents(lineState.unitPrice) === asMoneyCents(product.price)) {
          lineState.priceOverrideReason = "";
        }
        cart.set(product.id, lineState);
        renderCart();
      });
      line.querySelector("button").addEventListener("click", () => {
        cart.delete(product.id);
        renderCart();
      });
      cartLines.appendChild(line);
    });
    cartGrandTotal = Number(total.toFixed(2));
    cartTotal.textContent = money(cartGrandTotal);
    cartJson.value = JSON.stringify(
      Array.from(cart.values()).map((lineState) => ({
        product_id: lineState.product.id,
        quantity: lineState.quantity,
        unit_price: Number(lineState.unitPrice.toFixed(2)),
        price_override_reason: lineState.priceOverrideReason || "",
      }))
    );
    syncStockFeedback();
    updateCashTenderUI();
    if (!isSubmittingSale) {
      saveDraft();
    }
  }

  function ensureOverrideReasons() {
    for (const lineState of cart.values()) {
      const isPriceOverride = asMoneyCents(lineState.unitPrice) !== asMoneyCents(lineState.product.price);
      if (!isPriceOverride) continue;
      if (String(lineState.priceOverrideReason || "").trim()) continue;

      const capturedReason = window.prompt(
        `Captura el motivo del ajuste de precio para ${lineState.product.name}:`
      );
      const normalizedReason = String(capturedReason || "").trim();
      if (!normalizedReason) {
        return { ok: false, productName: lineState.product.name };
      }
      lineState.priceOverrideReason = normalizedReason;
    }
    return { ok: true };
  }

  productButtons.forEach((button) => {
    button.addEventListener("click", () => addProduct(productFromButton(button)));
  });

  searchInput.addEventListener("input", () => {
    const query = normalize(searchInput.value);
    productButtons.forEach((button) => {
      const product = productFromButton(button);
      const visible =
        !query ||
        normalize(product.name).includes(query) ||
        normalize(product.barcode).includes(query) ||
        normalize(product.sku).includes(query);
      button.hidden = !visible;
    });
  });

  searchInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const product = findProduct(searchInput.value);
    if (product) {
      addProduct(product);
      searchInput.value = "";
      productButtons.forEach((button) => (button.hidden = false));
      return;
    }
    if (searchInput.form) {
      searchInput.form.requestSubmit();
    }
  });

  paymentMethod.addEventListener("change", () => {
    syncCustomerRequirement();
    updateCashTenderUI();
  });

  if (customerSelect) {
    customerSelect.addEventListener("change", saveDraft);
  }

  if (tenderedAmountInput) {
    tenderedAmountInput.addEventListener("input", () => {
      updateCashTenderUI();
    });
  }

  if (resetTenderedButton) {
    resetTenderedButton.addEventListener("click", () => {
      setTenderedAmount(0);
      updateCashTenderUI();
      if (tenderedAmountInput) {
        tenderedAmountInput.focus();
      }
    });
  }

  billButtons.forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.billExact === "1") {
        setTenderedAmount(cartGrandTotal);
        updateCashTenderUI();
        if (tenderedAmountInput) {
          tenderedAmountInput.focus();
        }
        return;
      }
      const billAmount = parseAmount(button.dataset.billAmount);
      setTenderedAmount(readTenderedAmount() + billAmount);
      updateCashTenderUI();
      if (tenderedAmountInput) {
        tenderedAmountInput.focus();
      }
    });
  });

  clearCart.addEventListener("click", () => {
    cart.clear();
    setTenderedAmount(0);
    clearFeedback();
    renderCart();
    clearPersistedDraft();
    searchInput.focus();
  });

  posForm.addEventListener("submit", (event) => {
    if (!hasOpenCashSession) {
      event.preventDefault();
      showFeedback("Abre la caja antes de registrar ventas.", "error");
      return;
    }
    if (cart.size === 0) {
      event.preventDefault();
      showFeedback("Agrega productos al ticket.", "error");
      return;
    }
    const reasonValidation = ensureOverrideReasons();
    if (!reasonValidation.ok) {
      event.preventDefault();
      showFeedback(`Captura el motivo del ajuste de precio para ${reasonValidation.productName}.`, "error");
      return;
    }
    if (paymentMethod.value === "credit" && customerSelect && !customerSelect.value) {
      event.preventDefault();
      showFeedback("Selecciona un cliente para vender a crédito.", "error");
      customerSelect.focus();
      return;
    }
    if (paymentMethod.value === "cash") {
      const tendered = readTenderedAmount();
      if (tendered <= 0) {
        event.preventDefault();
        showFeedback("Captura el monto recibido en efectivo.", "error");
        return;
      }
      if (tendered + 0.0001 < cartGrandTotal) {
        event.preventDefault();
        showFeedback(`Monto insuficiente. Faltan ${money(cartGrandTotal - tendered)} para completar la venta.`, "error");
        return;
      }
    }
    isSubmittingSale = true;
    clearPersistedDraft();
    renderCart();
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "Cobrando...";
    }
  });

  if (isSaleSaved) {
    cart.clear();
    setTenderedAmount(0);
    clearPersistedDraft();
  } else {
    restoreDraft();
  }

  renderCart();
  syncCustomerRequirement();
  setTenderedAmount(readTenderedAmount());
  updateCashTenderUI();
})();
