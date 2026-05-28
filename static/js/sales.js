(function () {
  const productButtons = Array.from(document.querySelectorAll(".product-tile"));
  const searchInput = document.getElementById("sale-product-search");
  const productSearchForm = document.getElementById("sale-product-search-form");
  const cartLines = document.getElementById("sale-cart-lines");
  const cartJson = document.getElementById("sale-cart-json");
  const initialCartNode = document.getElementById("sale-initial-cart");
  const saleForm = document.getElementById("sale-form");
  const clearCart = document.getElementById("sale-clear-cart");
  const feedback = document.getElementById("sale-feedback");
  const subtotalNode = document.getElementById("sale-subtotal");
  const totalNode = document.getElementById("sale-total");
  const generalDiscountInput = document.getElementById("sale-discount-total");
  const customerSearchInput = document.getElementById("customer-search");
  const customerIdInput = document.getElementById("customer-id");
  const customerOptions = document.getElementById("customer-options");
  const customerSummary = document.getElementById("customer-selected-info");
  const shippingAddress = document.getElementById("shipping-address");
  const paymentMethodInput = saleForm ? saleForm.querySelector('[name="payment_method"]') : null;
  const notesInput = saleForm ? saleForm.querySelector('[name="notes"]') : null;
  const searchStateCartJson = document.getElementById("sale-search-cart-json");
  const searchStateCustomerId = document.getElementById("sale-search-customer-id");
  const searchStateCustomerName = document.getElementById("sale-search-customer-name");
  const searchStateShippingAddress = document.getElementById("sale-search-shipping-address");
  const searchStatePaymentMethod = document.getElementById("sale-search-payment-method");
  const searchStateNotes = document.getElementById("sale-search-notes");
  const searchStateDiscountTotal = document.getElementById("sale-search-discount-total");
  const cart = new Map();
  const customerRecords = customerOptions
    ? Array.from(customerOptions.querySelectorAll(".customer-option")).map((option) => ({
        id: option.dataset.id || "",
        name: (option.dataset.name || option.textContent || "").trim(),
        display: (option.dataset.display || option.dataset.name || "").trim(),
        address: (option.dataset.address || "").trim(),
        phone: (option.dataset.phone || "").trim(),
        taxId: (option.dataset.taxId || "").trim(),
        contactName: (option.dataset.contactName || "").trim(),
        search: normalize(option.dataset.search || option.dataset.display || option.textContent || ""),
        optionNode: option,
      }))
    : [];
  const selectedCustomerRecord = customerIdInput
    ? customerRecords.find((record) => record.id === String(customerIdInput.value || ""))
    : null;
  const customerState = {
    selectedAddress: selectedCustomerRecord ? selectedCustomerRecord.address : "",
    customAddressTyped: false,
    filteredRecords: [],
    activeIndex: -1,
  };

  function normalize(value) {
    return String(value || "").trim().toLowerCase();
  }

  function money(value) {
    return new Intl.NumberFormat("es-MX", {
      style: "currency",
      currency: "MXN",
    }).format(value);
  }

  function parseInitialCartItems() {
    if (!initialCartNode) return [];
    try {
      const parsed = JSON.parse(initialCartNode.textContent || "[]");
      return Array.isArray(parsed) ? parsed : [];
    } catch (_error) {
      return [];
    }
  }

  function hydrateCart() {
    parseInitialCartItems().forEach((line) => {
      const productId = Number(line.product_id);
      const quantity = Number(line.quantity);
      if (!productId || !quantity || quantity <= 0) return;
      cart.set(productId, {
        product: {
          id: productId,
          name: line.name || "",
          barcode: line.barcode || "",
          sku: line.sku || "",
          price: Number(line.price || 0),
          stock: Number(line.stock || 0),
        },
        quantity,
        discount: Math.max(0, Number(line.discount || 0)),
      });
    });
  }

  function syncProductSearchState() {
    if (!productSearchForm) return;
    if (searchStateCartJson && cartJson) {
      searchStateCartJson.value = cartJson.value || "[]";
    }
    if (searchStateCustomerId && customerIdInput) {
      searchStateCustomerId.value = customerIdInput.value || "";
    }
    if (searchStateCustomerName && customerSearchInput) {
      searchStateCustomerName.value = customerSearchInput.value || "";
    }
    if (searchStateShippingAddress && shippingAddress) {
      searchStateShippingAddress.value = shippingAddress.value || "";
    }
    if (searchStatePaymentMethod && paymentMethodInput) {
      searchStatePaymentMethod.value = paymentMethodInput.value || "";
    }
    if (searchStateNotes && notesInput) {
      searchStateNotes.value = notesInput.value || "";
    }
    if (searchStateDiscountTotal && generalDiscountInput) {
      searchStateDiscountTotal.value = generalDiscountInput.value || "0.00";
    }
  }

  function showFeedback(message, level) {
    if (!feedback) return;
    feedback.textContent = message;
    feedback.classList.remove("is-hidden", "error", "warn");
    feedback.classList.add(level || "warn");
  }

  function clearFeedback() {
    if (!feedback) return;
    feedback.textContent = "";
    feedback.classList.add("is-hidden");
    feedback.classList.remove("error", "warn");
  }

  function updateCustomerSummary(customerRecord) {
    if (!customerSummary) return;
    customerSummary.replaceChildren();
    if (!customerRecord) return;

    const list = document.createElement("ul");
    list.className = "customer-summary-list";

    [
      customerRecord.name,
      customerRecord.contactName ? "Contacto: " + customerRecord.contactName : "",
      customerRecord.phone ? "Teléfono: " + customerRecord.phone : "",
      customerRecord.taxId ? "RFC: " + customerRecord.taxId : "",
      customerRecord.address ? "Dirección registrada: " + customerRecord.address : "",
    ]
      .filter(Boolean)
      .forEach((value) => {
        const item = document.createElement("li");
        item.textContent = value;
        list.appendChild(item);
      });

    customerSummary.appendChild(list);
  }

  function findCustomerRecordByValue(value) {
    const normalizedValue = normalize(value);
    if (!normalizedValue) return null;

    const exactRecord = customerRecords.find((record) => normalize(record.display) === normalizedValue);
    if (exactRecord) return exactRecord;

    const exactNameMatches = customerRecords.filter((record) => normalize(record.name) === normalizedValue);
    if (exactNameMatches.length === 1) {
      return exactNameMatches[0];
    }

    return null;
  }

  function closeCustomerOptions() {
    if (!customerOptions || !customerSearchInput) return;
    customerState.filteredRecords = [];
    customerState.activeIndex = -1;
    customerSearchInput.setAttribute("aria-expanded", "false");
    customerSearchInput.removeAttribute("aria-activedescendant");
    customerOptions.classList.add("is-hidden");
    customerRecords.forEach((record) => {
      record.optionNode.hidden = true;
      record.optionNode.classList.remove("active");
      record.optionNode.setAttribute("aria-selected", "false");
    });
  }

  function setActiveCustomerOption(index) {
    if (!customerSearchInput || !customerState.filteredRecords.length) return;
    customerState.activeIndex = index;
    customerState.filteredRecords.forEach((record, recordIndex) => {
      const isActive = recordIndex === index;
      record.optionNode.classList.toggle("active", isActive);
      record.optionNode.setAttribute("aria-selected", isActive ? "true" : "false");
      if (isActive) {
        customerSearchInput.setAttribute("aria-activedescendant", record.optionNode.id);
      }
    });
  }

  function openCustomerOptions(query) {
    if (!customerOptions || !customerSearchInput) return;
    const normalizedQuery = normalize(query);
    const filteredRecords = customerRecords
      .filter((record) => !normalizedQuery || record.search.includes(normalizedQuery))
      .slice(0, 8);

    if (!filteredRecords.length) {
      closeCustomerOptions();
      return;
    }

    const visibleIds = new Set(filteredRecords.map((record) => record.id));
    customerRecords.forEach((record) => {
      const visible = visibleIds.has(record.id);
      record.optionNode.hidden = !visible;
      record.optionNode.classList.remove("active");
      record.optionNode.setAttribute("aria-selected", "false");
    });

    customerState.filteredRecords = filteredRecords;
    customerOptions.classList.remove("is-hidden");
    customerSearchInput.setAttribute("aria-expanded", "true");
    setActiveCustomerOption(0);
  }

  function selectCustomerRecord(customerRecord) {
    if (!customerRecord || !customerIdInput || !customerSearchInput) return;
    customerIdInput.value = customerRecord.id;
    customerSearchInput.value = customerRecord.display;
    updateCustomerSummary(customerRecord);
    closeCustomerOptions();
    const nextAddress = customerRecord.address;
    const currentAddress = shippingAddress ? shippingAddress.value.trim() : "";
    const shouldReplaceAddress =
      !currentAddress || currentAddress === customerState.selectedAddress || !customerState.customAddressTyped;

    if (shippingAddress && shouldReplaceAddress) {
      shippingAddress.value = nextAddress;
      customerState.customAddressTyped = false;
    }
    customerState.selectedAddress = nextAddress;
  }

  function productFromButton(button) {
    return {
      id: Number(button.dataset.productId),
      name: button.dataset.name,
      barcode: button.dataset.barcode,
      sku: button.dataset.sku,
      price: Number(button.dataset.price),
      stock: Number(button.dataset.stock),
    };
  }

  function addProduct(product) {
    const current = cart.get(product.id) || {
      product,
      quantity: 1,
      discount: 0,
    };
    if (cart.has(product.id)) {
      current.quantity += 1;
    }
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

  function syncStockFeedback() {
    const oversoldLine = Array.from(cart.values()).find(({ product, quantity }) => quantity > product.stock);
    if (oversoldLine) {
      const { product } = oversoldLine;
      showFeedback(
        product.stock <= 0
          ? product.name + " ya no tiene existencias. La venta se registrara de todos modos."
          : product.name + " solo tiene " + product.stock + " en existencia. La venta se registrara de todos modos.",
        "warn"
      );
      return;
    }
    clearFeedback();
  }

  function selectedCustomerRecordFromInput() {
    if (!customerSearchInput || !customerIdInput) return null;
    if (!normalize(customerSearchInput.value)) {
      customerIdInput.value = "";
      updateCustomerSummary(null);
      return null;
    }

    const customerRecord = findCustomerRecordByValue(customerSearchInput.value);
    if (customerRecord) {
      customerIdInput.value = customerRecord.id;
      updateCustomerSummary(customerRecord);
      return customerRecord;
    }

    customerIdInput.value = "";
    updateCustomerSummary(null);
    return null;
  }

  function updateShippingAddress() {
    const customerRecord = selectedCustomerRecordFromInput();
    if (!customerRecord) return;
    selectCustomerRecord(customerRecord);
  }

  function renderCart() {
    cartLines.innerHTML = "";
    let subtotal = 0;
    let lineDiscounts = 0;

    cart.forEach((line) => {
      const lineSubtotal = line.product.price * line.quantity;
      const maxDiscount = Math.max(0, lineSubtotal);
      if (line.discount > maxDiscount) {
        line.discount = maxDiscount;
      }
      const lineTotal = lineSubtotal - line.discount;
      subtotal += lineSubtotal;
      lineDiscounts += line.discount;

      const row = document.createElement("div");
      row.className = "sale-cart-line";
      row.innerHTML = `
        <div class="sale-line-product">
          <strong title="${line.product.name}">${line.product.name}</strong>
          <small>${money(line.product.price)} c/u</small>
        </div>
        <label class="sale-line-field">
          <span>Cantidad</span>
          <input type="number" min="0.001" step="any" data-step-one="true" value="${line.quantity}" aria-label="Cantidad de ${line.product.name}">
        </label>
        <label class="sale-line-field">
          <span>Descuento</span>
          <input type="number" min="0" step="0.01" value="${line.discount.toFixed(2)}" aria-label="Descuento de ${line.product.name}">
        </label>
        <div class="sale-line-total">
          <span>Total</span>
          <strong>${money(lineTotal)}</strong>
        </div>
        <button type="button" title="Quitar" aria-label="Quitar ${line.product.name}">x</button>
      `;

      const [quantityInput, discountInput] = row.querySelectorAll("input");
      quantityInput.addEventListener("change", () => {
        const nextQuantity = Number(quantityInput.value);
        if (!nextQuantity || nextQuantity <= 0) {
          cart.delete(line.product.id);
        } else {
          line.quantity = nextQuantity;
          cart.set(line.product.id, line);
        }
        renderCart();
      });
      discountInput.addEventListener("change", () => {
        const nextDiscount = Number(discountInput.value || 0);
        line.discount = nextDiscount > 0 ? nextDiscount : 0;
        cart.set(line.product.id, line);
        renderCart();
      });
      row.querySelector("button").addEventListener("click", () => {
        cart.delete(line.product.id);
        renderCart();
      });

      cartLines.appendChild(row);
    });

    const generalDiscount = Number(generalDiscountInput.value || 0);
    const total = Math.max(0, subtotal - lineDiscounts - generalDiscount);
    subtotalNode.textContent = money(subtotal);
    totalNode.textContent = money(total);
    cartJson.value = JSON.stringify(
      Array.from(cart.values()).map((line) => ({
        product_id: line.product.id,
        quantity: line.quantity,
        unit_price: line.product.price,
        discount_amount: Number(line.discount.toFixed(2)),
      }))
    );
    syncStockFeedback();
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
      productButtons.forEach((button) => {
        button.hidden = false;
      });
      return;
    }
    if (searchInput.form) {
      searchInput.form.requestSubmit();
    }
  });

  if (productSearchForm) {
    productSearchForm.addEventListener("submit", () => {
      syncProductSearchState();
    });
  }

  if (customerSearchInput) {
    customerSearchInput.addEventListener("focus", () => {
      if (customerRecords.length) {
        openCustomerOptions(customerSearchInput.value);
      }
    });
    customerSearchInput.addEventListener("input", () => {
      const exactRecord = findCustomerRecordByValue(customerSearchInput.value);
      if (exactRecord) {
        customerIdInput.value = exactRecord.id;
        updateCustomerSummary(exactRecord);
      } else if (customerIdInput) {
        customerIdInput.value = "";
        updateCustomerSummary(null);
      }
      openCustomerOptions(customerSearchInput.value);
    });
    customerSearchInput.addEventListener("change", updateShippingAddress);
    customerSearchInput.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        if (!customerState.filteredRecords.length) {
          openCustomerOptions(customerSearchInput.value);
        }
        if (customerState.filteredRecords.length) {
          event.preventDefault();
          const nextIndex = (customerState.activeIndex + 1) % customerState.filteredRecords.length;
          setActiveCustomerOption(nextIndex);
        }
        return;
      }
      if (event.key === "ArrowUp") {
        if (!customerState.filteredRecords.length) {
          openCustomerOptions(customerSearchInput.value);
        }
        if (customerState.filteredRecords.length) {
          event.preventDefault();
          const nextIndex =
            (customerState.activeIndex - 1 + customerState.filteredRecords.length) % customerState.filteredRecords.length;
          setActiveCustomerOption(nextIndex);
        }
        return;
      }
      if (event.key === "Escape") {
        closeCustomerOptions();
        return;
      }
      if (event.key !== "Enter") return;
      const activeRecord =
        customerState.filteredRecords[customerState.activeIndex] ||
        findCustomerRecordByValue(customerSearchInput.value) ||
        customerState.filteredRecords[0];
      if (!activeRecord) return;
      event.preventDefault();
      selectCustomerRecord(activeRecord);
    });
  }

  if (customerOptions) {
    customerOptions.addEventListener("mousedown", (event) => {
      const option = event.target.closest(".customer-option");
      if (option) {
        event.preventDefault();
      }
    });
    customerOptions.addEventListener("click", (event) => {
      const option = event.target.closest(".customer-option");
      if (!option) return;
      const customerRecord = customerRecords.find((record) => record.optionNode === option);
      selectCustomerRecord(customerRecord);
    });
  }

  document.addEventListener("click", (event) => {
    if (!customerSearchInput || !customerOptions) return;
    if (customerSearchInput.contains(event.target) || customerOptions.contains(event.target)) return;
    closeCustomerOptions();
  });

  if (shippingAddress) {
    shippingAddress.addEventListener("input", () => {
      customerState.customAddressTyped = true;
    });
  }

  generalDiscountInput.addEventListener("input", renderCart);
  clearCart.addEventListener("click", () => {
    cart.clear();
    renderCart();
  });

  saleForm.addEventListener("submit", (event) => {
    if (cart.size === 0) {
      event.preventDefault();
      showFeedback("Agrega al menos un producto a la venta.", "error");
      return;
    }
    selectedCustomerRecordFromInput();
    if (!customerIdInput || !customerIdInput.value) {
      event.preventDefault();
      showFeedback("Selecciona un cliente válido de la lista para continuar.", "error");
    }
  });

  if (customerSearchInput && selectedCustomerRecord && !customerSearchInput.value.trim()) {
    customerSearchInput.value = selectedCustomerRecord.display;
  }

  hydrateCart();
  updateCustomerSummary(selectedCustomerRecord || selectedCustomerRecordFromInput());
  if (selectedCustomerRecord) {
    customerState.selectedAddress = selectedCustomerRecord.address;
  } else {
    updateShippingAddress();
  }
  closeCustomerOptions();
  renderCart();
  syncProductSearchState();
})();
