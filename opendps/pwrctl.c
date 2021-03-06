/*
 * The MIT License (MIT)
 *
 * Copyright (c) 2017 Johan Kanflo (github.com/kanflo)
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

#include "pwrctl.h"
#include "dps-model.h"
#include "pastunits.h"
#include "past.h"
#include <gpio.h>
#include <dac.h>

/** This module handles voltage and current calculations
  * Calculations based on measurements found at
  * https://docs.google.com/spreadsheets/d/1AhGsU_gvZjqZyr2ZYrnkz6BeUqMquzh9UNYoTqy_Zp4/edit?usp=sharing
  */

static uint32_t i_out, v_out, i_limit;
float A_ADC_K_COEF = A_ADC_K;
float A_ADC_C_COEF = A_ADC_C;
float A_DAC_K_COEF = A_DAC_K;
float A_DAC_C_COEF = A_DAC_C;
float V_ADC_K_COEF = V_ADC_K;
float V_ADC_C_COEF = V_ADC_C;
float V_DAC_K_COEF = V_DAC_K;
float V_DAC_C_COEF = V_DAC_C;
float VIN_ADC_K_COEF = VIN_ADC_K;
float VIN_ADC_C_COEF = VIN_ADC_C;
static bool v_out_enabled;

/** not static as it is referred to from hw.c for performance reasons */
uint32_t pwrctl_i_limit_raw;

/**
  * @brief Initialize the power control module
  * @retval none
  */
void pwrctl_init(past_t *past)
{
    //pwrctl_enable_vout(false);
   // return;
    //Load constants from PAST here
    float *p = 0;
    uint32_t length;
    if (past_read_unit(past, cal_A_ADC_K, (const void**) &p, &length))
    {
        A_ADC_K_COEF = *p;
    } else {
        A_ADC_K_COEF = A_ADC_K;
    }

    if (past_read_unit(past, cal_A_ADC_C,(const void**) &p, &length))
    {
        A_ADC_C_COEF = *p;
    } else {
        A_ADC_C_COEF = A_ADC_C;
    }
    
    if (past_read_unit(past, cal_A_DAC_K, (const void**) &p, &length))
    {
        A_DAC_K_COEF = *p;
    } else {
        A_DAC_K_COEF = A_DAC_K;
    }
    
    if (past_read_unit(past, cal_A_DAC_C,(const void**) &p, &length))
    {
        A_DAC_C_COEF = *p;
    } else {
        A_DAC_C_COEF = A_DAC_C;
    }
    
    if (past_read_unit(past, cal_V_ADC_K, (const void**) &p, &length))
    {
        V_ADC_K_COEF = *p;
    } else {
        V_ADC_K_COEF = V_ADC_K;
    }
    
    if (past_read_unit(past, cal_V_ADC_C,(const void**) &p, &length))
    {
        V_ADC_C_COEF = *p;
    } else {
        V_ADC_C_COEF = V_ADC_C;
    }
    
    if (past_read_unit(past, cal_V_DAC_K, (const void**) &p, &length))
    {
        V_DAC_K_COEF = *p;
    } else {
        V_DAC_K_COEF = V_DAC_K;
    }
    
    if (past_read_unit(past, cal_V_DAC_C,(const void**) &p, &length))
    {
        V_DAC_C_COEF = *p;
    } else {
        V_DAC_C_COEF = V_DAC_C;
    }
    
    if (past_read_unit(past, cal_VIN_ADC_K,(const void**) &p, &length))
    {
        VIN_ADC_K_COEF = *p;
    } else {
        VIN_ADC_K_COEF = VIN_ADC_K;
    }
    
    if (past_read_unit(past, cal_VIN_ADC_C,(const void**) &p, &length))
    {
        VIN_ADC_C_COEF = *p;
    } else {
        VIN_ADC_C_COEF = VIN_ADC_C;
    }

    pwrctl_enable_vout(false);

}

/**
  * @brief Set voltage output
  * @param value_mv voltage in milli volt
  * @retval true requested voltage was within specs
  */
bool pwrctl_set_vout(uint32_t value_mv)
{
    /** @todo Check with max Vout, currently filtered by ui.c */
    v_out = value_mv;
    if (v_out_enabled) {
        /** Needed for the DPS5005 "communications version" (the one with BT/USB) */
        DAC_DHR12R1 = pwrctl_calc_vout_dac(v_out);
    } else {
        DAC_DHR12R1 = 0;
    }
    return true;
}

/**
  * @brief Set current output
  * @param current_ma current in milli ampere
  * @retval true requested current was within specs
  */
bool pwrctl_set_iout(uint32_t value_ma)
{
    i_out = value_ma;
    if (v_out_enabled) {
        DAC_DHR12R2 = pwrctl_calc_iout_dac(value_ma);
    } else {
        DAC_DHR12R2 = 0;
    }
    return true;
}

/**
  * @brief Get current output setting
  * @retval current setting in milli amps
  */
uint32_t pwrctl_get_iout(void)
{
    return i_out;
}

/**
  * @brief Get voltage output setting
  * @retval current setting in milli volt
  */
uint32_t pwrctl_get_vout(void)
{
    return v_out;
}

/**
  * @brief Set current limit
  * @param value_ma limit in milliampere
  * @retval true requested current was within specs
  */
bool pwrctl_set_ilimit(uint32_t value_ma)
{
    /** @todo Check with I_limit, currently filtered by ui.c */
    i_limit = value_ma;
    pwrctl_i_limit_raw = pwrctl_calc_ilimit_adc(i_limit);
    return true;
}

/**
  * @brief Get current limit setting
  * @retval current setting in milliampere
  */
uint32_t pwrctl_get_ilimit(void)
{
    return i_limit;
}

/**
  * @brief Enable or disable power output
  * @param enable true for enable, false for disable
  * @retval none
  */
void pwrctl_enable_vout(bool enable)
{
    v_out_enabled = enable;
    if (v_out_enabled) {
      (void) pwrctl_set_vout(v_out);
      (void) pwrctl_set_iout(i_out);
#ifdef DPS5015
        //gpio_clear(GPIOA, GPIO9); // this is power control on '5015
        gpio_set(GPIOB, GPIO11);    // B11 is fan control on '5015
        gpio_clear(GPIOC, GPIO13);  // C13 is power control on '5015
#else
        gpio_clear(GPIOB, GPIO11);  // B11 is power control on '5005
#endif
    } else {
#ifdef DPS5015
        //gpio_set(GPIOA, GPIO9);    // gpio_set(GPIOB, GPIO11);
        gpio_clear(GPIOB, GPIO11); // B11 is fan control on '5015
        gpio_set(GPIOC, GPIO13);   // C13 is power control on '5015
#else
        gpio_set(GPIOB, GPIO11);  // B11 is power control on '5005
#endif
      (void) pwrctl_set_vout(v_out);
      (void) pwrctl_set_iout(i_out);
    }
}

/**
  * @brief Return power output status
  * @retval true if power output is wnabled
  */
bool pwrctl_vout_enabled(void)
{
    return v_out_enabled;
}

/**
  * @brief Calculate V_in based on raw ADC measurement
  * @param raw value from ADC
  * @retval corresponding voltage in milli volt
  */
uint32_t pwrctl_calc_vin(uint16_t raw)
{
    return VIN_ADC_K_COEF*raw + VIN_ADC_C_COEF; /** @todo: Determine if we still need to trim. */
}

/**
  * @brief Calculate V_out based on raw ADC measurement
  * @param raw value from ADC
  * @retval corresponding voltage in milli volt
  */
uint32_t pwrctl_calc_vout(uint16_t raw)
{
    return V_ADC_K_COEF*raw + V_ADC_C_COEF;
}

/**
  * @brief Calculate DAC setting for requested V_out
  * @param v_out_mv requested output voltage
  * @retval corresponding voltage in milli volt
  */
uint16_t pwrctl_calc_vout_dac(uint32_t v_out_mv)
{
    uint32_t dac = V_DAC_K_COEF*v_out_mv + V_DAC_C_COEF;
    return dac & 0xfff; /** 12 bits */
}

/**
  * @brief Calculate I_out based on raw ADC measurement
  * @param raw value from ADC
  * @retval corresponding current in milliampere
  */
uint32_t pwrctl_calc_iout(uint16_t raw)
{
    return A_ADC_K_COEF*raw + A_ADC_C_COEF;
}

/**
  * @brief Calculate expected raw ADC value based on selected I_limit
  * @param i_limit_ma selected I_limit
  * @retval expected raw ADC value
  */
uint16_t pwrctl_calc_ilimit_adc(uint16_t i_limit_ma)
{
    return (i_limit_ma - A_ADC_C_COEF) / A_ADC_K_COEF + 1;
}

/**
  * @brief Calculate DAC setting for constant current mode
  * @param i_out_ma requested constant current
  * @retval corresponding DAC value
  * @note this formula is valid for the DPS5005 and would probably need changes
  *       for DPS:es capable of higher current output.
  */
uint16_t pwrctl_calc_iout_dac(uint32_t i_out_ma)
{
    uint32_t dac = A_DAC_K_COEF * i_out_ma + A_DAC_C_COEF;
    return dac & 0xfff; /** 12 bits */
}


