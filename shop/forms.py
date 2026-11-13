from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.validators import RegexValidator
from .models import Address, Enquiry, Review, ReturnRequest


class RegisterForm(UserCreationForm):
    email = forms.EmailField()

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "first_name",
            "last_name",
            "password1",
            "password2",
        ]

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("This email already has an account.")
        return email


class AddressForm(forms.ModelForm):
    phone = forms.CharField(
        validators=[RegexValidator(r"^\+?[0-9]{10,13}$", "Enter a valid phone number.")]
    )
    postcode = forms.CharField(
        validators=[
            RegexValidator(
                r"^[1-9][0-9]{5}$", "Enter a valid six-digit Indian PIN code."
            )
        ]
    )

    class Meta:
        model = Address
        exclude = ["user"]


class CheckoutForm(AddressForm):
    email = forms.EmailField()


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ["rating", "text"]
        widgets = {
            "rating": forms.Select(
                choices=[(i, str(i) + " stars") for i in range(1, 6)]
            )
        }


class ReturnForm(forms.ModelForm):
    class Meta:
        model = ReturnRequest
        fields = ["kind", "reason", "requested_size"]

    def clean(self):
        data = super().clean()
        if data.get("kind") == "exchange" and not data.get("requested_size"):
            self.add_error("requested_size", "Enter your preferred size.")
        return data


class ContactForm(forms.ModelForm):
    class Meta:
        model = Enquiry
        fields = ["name", "email", "message"]
