(function () {
  const productButtons = Array.from(document.querySelectorAll(".product-tile"));
  const searchInput = document.getElementById("sale-product-search");
  const cartLines = document.getElementById("sale-cart-lines");
  const cartJson = document.getElementById("sale-cart-json");
  const saleForm = document.getElementById("sale-form");
  const clearCart = document.getElementById("sale-clear-cart");
  const feedback = document.getElementById("sale-feedback");
  const subtotalNode = document.getElementById("sale-subtotal");
  const totalNode = document.getElementById("sale-total");
  const generalDiscountInput = document.getElementById("sale-discount-total");
  const customerSearchInput = document.getElementById("customer-search");
  const customerSelect = document.getElementById("customer-id");
  const shippingAddress = document.getElementById("shipping-address");
  const cart = new Map();
  const selectedCustomerOption = customerSelect
    ? customerSelect.querySelector('option[value="' + customerSelect.value + '"]')
    : null;
  const customerState = {
    selectedAddress: selectedCustomerOption ? (selectedCustomerOption.dataset.address || "").trim() : "",
    customAddressTyped: false,
  };
  const customerOptions = customerSelect
    ? Array.from(customerSelect.options)
        .filter((option) => option.value)
        .map((option) => ({
          value: option.value,
          label: option.textContent,
          address: option.dataset.address || "",
          search: normalize((option.dataset.search || option.textContent || "").toLowerCase()),
        }))
    : [];

  function normalize(value) {
    return String(value || "").trim().toLowerCase();
  }

  function money(value) {
    return new Intl.NumberFormat("es-MX", {
      style: "currency",
      currency: "MXN",
    }).format(value);
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

  function updateShippingAddress() {
    if (!customerSelect || !shippingAddress) return;
    const option = customerSelect.options[customerSelect.selectedIndex];
    if (!option) return;
    const nextAddress = (option.dataset.address || "").trim();
    const currentAddress = shippingAddress.value.trim();
    const shouldReplaceAddress =
      !currentAddress || currentAddress === customerState.selectedAddress || !customerState.customAddressTyped;

    if (!shouldReplaceAddress) return;
    shippingAddress.value = nextAddress;
    customerState.selectedAddress = nextAddress;
    customerState.customAddressTyped = false;
  }

  function renderCustomerOptions() {
    if (!customerSelect) return;
    const selectedValue = customerSelect.value;
    const query = normalize(customerSearchInput ? customerSearchInput.value : "");
    const filtered = customerOptions.filter((option) => !query || option.search.includes(query));
    const selectedOptionMissing = selectedValue && !filtered.some((option) => option.value === selectedValue);
    const entries = selectedOptionMissing
      ? [customerOptions.find((option) => option.value === selectedValue), ...filtered].filter(Boolean)
      : filtered;

    customerSelect.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Selecciona cliente";
    customerSelect.appendChild(placeholder);

    entries.forEach((optionData) => {
      const option = document.createElement("option");
      option.value = optionData.value;
      option.textContent = optionData.label;
      option.dataset.address = optionData.address;
      option.dataset.search = optionData.search;
      if (optionData.value === selectedValue) {
        option.selected = true;
      }
      customerSelect.appendChild(option);
    });
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
        <div>
          <strong title="${line.product.name}">${line.product.name}</strong>
          <small>${money(line.product.price)} c/u</small>
        </div>
        <input type="number" min="0.001" step="0.001" value="${line.quantity}">
        <input type="number" min="0" step="0.01" value="${line.discount.toFixed(2)}">
        <span>${money(lineTotal)}</span>
        <button type="button" title="Quitar">x</button>
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
    }
  });

  if (customerSearchInput) {
    customerSearchInput.addEventListener("input", renderCustomerOptions);
  }

  if (shippingAddress) {
    shippingAddress.addEventListener("input", () => {
      customerState.customAddressTyped = true;
    });
  }

  customerSelect.addEventListener("change", updateShippingAddress);
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
    if (!customerSelect.value) {
      event.preventDefault();
      showFeedback("Selecciona un cliente para continuar.", "error");
    }
  });

  renderCustomerOptions();
  updateShippingAddress();
  renderCart();
})();
